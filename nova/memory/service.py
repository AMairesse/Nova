from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import yaml
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F, Q
from django.utils import timezone

from nova.llm.embeddings import aget_embeddings_provider
from nova.llm.hybrid_search import (
    blend_semantic_fts,
    minmax_bounds,
    minmax_normalize,
    resolve_query_vector,
    semantic_similarity_from_distance,
)
from nova.models.Memory import (
    MemoryItem,
    MemoryItemEmbedding,
    MemoryItemStatus,
    MemoryItemType,
    MemoryTheme,
)

MEMORY_ROOT = "/memory"
MEMORY_README_PATH = "/memory/README.md"
MEMORY_ALLOWED_EXTENSIONS = {".md", ".txt"}
FRONTMATTER_OPEN = "---\n"
DEFAULT_MEMORY_EXTENSION = ".md"

MEMORY_README_CONTENT = """# Memory

`/memory` is a user-scoped virtual directory shared across the current user's
React Terminal agents that have memory access.

Use it like this:
- `ls /memory`
- `ls /memory/<theme>`
- `cat /memory/<theme>/<file>.md`
- `grep -r "term" /memory`
- `memory search "conceptual query"`
- `tee /memory/<theme>/<file>.md --text "..."`

Use `grep` for lexical text matching.
Use `memory search` for hybrid lexical + embeddings retrieval.
"""


@dataclass(slots=True, frozen=True)
class MemoryPathSpec:
    kind: str
    normalized_path: str
    theme_slug: str | None = None
    filename: str | None = None
    extension: str | None = None


@dataclass(slots=True)
class MemoryVirtualEntry:
    path: str
    mime_type: str
    size: int
    item_id: int | None = None


def normalize_memory_theme_slug(theme: str) -> str:
    theme_value = (theme or "").strip().lower()
    if not theme_value:
        raise ValidationError("theme must be a non-empty string")
    return theme_value.replace(" ", "-")


def get_default_memory_theme_slug() -> str:
    return "general"


def build_default_memory_virtual_path(theme_slug: str, item_id: int) -> str:
    return posixpath.join(MEMORY_ROOT, theme_slug, f"{item_id}{DEFAULT_MEMORY_EXTENSION}")


def ensure_memory_virtual_path(item: MemoryItem) -> str:
    current = str(getattr(item, "virtual_path", "") or "").strip()
    if current:
        return current
    theme_slug = getattr(getattr(item, "theme", None), "slug", None) or get_default_memory_theme_slug()
    return build_default_memory_virtual_path(theme_slug, int(item.id))


def _humanize_theme_display_name(theme_slug: str) -> str:
    return str(theme_slug or "").replace("-", " ").strip().title() or get_default_memory_theme_slug().title()


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
    raise ValidationError("tags must be a string or a list of strings")


def _normalize_item_type(value: Any) -> str:
    item_type = str(value or "").strip().lower()
    if not item_type:
        raise ValidationError("type must be a non-empty string")
    if item_type not in set(MemoryItemType.values):
        raise ValidationError(f"Invalid memory item type: {item_type}")
    return item_type


def parse_memory_virtual_path(path: str) -> MemoryPathSpec:
    normalized = posixpath.normpath(str(path or "").strip() or MEMORY_ROOT)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized == MEMORY_ROOT:
        return MemoryPathSpec(kind="root", normalized_path=MEMORY_ROOT)
    if normalized == MEMORY_README_PATH:
        return MemoryPathSpec(kind="readme", normalized_path=MEMORY_README_PATH)
    if not normalized.startswith(f"{MEMORY_ROOT}/"):
        raise ValidationError("Not a memory path")

    relative = normalized[len(f"{MEMORY_ROOT}/"):]
    parts = [part for part in relative.split("/") if part]
    if len(parts) == 1:
        theme_slug = normalize_memory_theme_slug(parts[0])
        if parts[0] != theme_slug:
            raise ValidationError("Memory theme directory names must use normalized slugs")
        return MemoryPathSpec(kind="theme_dir", normalized_path=normalized, theme_slug=theme_slug)
    if len(parts) != 2:
        raise ValidationError("Memory paths support only one theme directory level")

    theme_slug = normalize_memory_theme_slug(parts[0])
    if parts[0] != theme_slug:
        raise ValidationError("Memory theme directory names must use normalized slugs")

    filename = parts[1]
    basename, extension = posixpath.splitext(filename)
    if not basename:
        raise ValidationError("Memory item filenames must not be empty")
    if extension.lower() not in MEMORY_ALLOWED_EXTENSIONS:
        raise ValidationError("Memory item files must use .md or .txt")

    return MemoryPathSpec(
        kind="item",
        normalized_path=normalized,
        theme_slug=theme_slug,
        filename=filename,
        extension=extension.lower(),
    )


def is_memory_path(path: str) -> bool:
    try:
        parse_memory_virtual_path(path)
        return True
    except ValidationError:
        return False


def _split_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source.startswith(FRONTMATTER_OPEN):
        return {}, source

    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", source, flags=re.DOTALL)
    if not match:
        return {}, source

    raw_frontmatter, body = match.groups()
    parsed = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValidationError("Memory frontmatter must be a YAML mapping")
    return parsed, body


def render_memory_document(item: MemoryItem) -> str:
    path = ensure_memory_virtual_path(item)
    theme_slug = getattr(getattr(item, "theme", None), "slug", None) or get_default_memory_theme_slug()
    frontmatter = {
        "id": item.id,
        "theme": str(theme_slug),
        "type": str(item.type),
        "tags": list(item.tags or []),
        "status": str(item.status),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "source_thread_id": getattr(item, "source_thread_id", None),
        "source_message_id": getattr(item, "source_message_id", None),
        "path": str(path),
    }
    rendered_frontmatter = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    body = str(item.content or "")
    if body:
        return f"---\n{rendered_frontmatter}\n---\n{body}"
    return f"---\n{rendered_frontmatter}\n---\n"


def _editable_fields_from_document(text: str) -> tuple[dict[str, Any], str]:
    frontmatter, body = _split_frontmatter_and_body(text)
    editable: dict[str, Any] = {}
    if "type" in frontmatter:
        editable["type"] = _normalize_item_type(frontmatter.get("type"))
    if "tags" in frontmatter:
        editable["tags"] = _normalize_tags(frontmatter.get("tags"))
    return editable, body


def _ensure_embedding_state(item: MemoryItem, *, embeddings_enabled: bool) -> str | None:
    if embeddings_enabled:
        embedding, _ = MemoryItemEmbedding.objects.get_or_create(
            user=item.user,
            item=item,
            defaults={
                "state": "pending",
                "dimensions": 1024,
            },
        )
        if embedding.state != "pending" or embedding.vector is not None or embedding.error:
            embedding.state = "pending"
            embedding.error = ""
            embedding.vector = None
            embedding.save(update_fields=["state", "error", "vector", "updated_at"])
        try:
            from nova.tasks.memory_tasks import compute_memory_item_embedding_task

            compute_memory_item_embedding_task.delay(embedding.id)
        except Exception:
            pass
        return embedding.state

    existing = getattr(item, "embedding", None)
    if existing is not None:
        existing.state = "pending"
        existing.error = ""
        existing.vector = None
        existing.save(update_fields=["state", "error", "vector", "updated_at"])
        return existing.state
    return None


def _get_or_create_theme(*, user, theme_slug: str) -> MemoryTheme:
    theme, _ = MemoryTheme.objects.get_or_create(
        user=user,
        slug=theme_slug,
        defaults={"display_name": _humanize_theme_display_name(theme_slug)},
    )
    return theme


async def list_themes_for_user(user, status: Optional[str] = None) -> dict[str, Any]:
    def _impl():
        status_value = (status or "").strip().lower() or MemoryItemStatus.ACTIVE
        valid_statuses = set(MemoryItemStatus.values)
        item_filter = Q(items__user=user)
        if status_value != "any":
            if status_value not in valid_statuses:
                status_value = MemoryItemStatus.ACTIVE
            item_filter &= Q(items__status=status_value)

        themes = (
            MemoryTheme.objects.filter(user=user)
            .filter(item_filter)
            .distinct()
            .order_by("slug")
        )
        return {
            "themes": [
                {
                    "slug": theme.slug,
                    "display_name": theme.display_name,
                    "description": theme.description,
                    "updated_at": theme.updated_at.isoformat() if theme.updated_at else None,
                }
                for theme in themes
            ]
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def add_memory_item(
    *,
    user,
    item_type: str,
    content: str,
    theme: Optional[str] = None,
    tags: Optional[list[str]] = None,
    source_thread=None,
    source_message=None,
    virtual_path: str | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    embeddings_enabled = await aget_embeddings_provider(user_id=user.id) is not None

    def _impl():
        normalized_type = _normalize_item_type(item_type)
        body = str(content or "")
        if not allow_empty and not body.strip():
            raise ValidationError("content must be a non-empty string")

        theme_value = (theme or "").strip() or get_default_memory_theme_slug()
        theme_slug = normalize_memory_theme_slug(theme_value)
        theme_obj = _get_or_create_theme(user=user, theme_slug=theme_slug)

        item = MemoryItem.objects.create(
            user=user,
            theme=theme_obj,
            type=normalized_type,
            content=body,
            tags=_normalize_tags(tags),
            source_thread=source_thread,
            source_message=source_message,
            virtual_path=str(virtual_path or "").strip(),
        )
        if not item.virtual_path:
            item.virtual_path = build_default_memory_virtual_path(theme_slug, int(item.id))
            item.save(update_fields=["virtual_path", "updated_at"])

        embedding_state = _ensure_embedding_state(item, embeddings_enabled=embeddings_enabled)
        return {
            "id": item.id,
            "embedding_state": embedding_state,
            "path": ensure_memory_virtual_path(item),
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def get_memory_item(item_id: int, *, user) -> dict[str, Any]:
    def _impl():
        item = (
            MemoryItem.objects.select_related("theme")
            .filter(user=user, id=item_id)
            .first()
        )
        if not item:
            return {"error": "not_found"}

        embedding = getattr(item, "embedding", None)
        return {
            "id": item.id,
            "path": ensure_memory_virtual_path(item),
            "theme": item.theme.slug if item.theme else None,
            "type": item.type,
            "content": item.content,
            "tags": item.tags,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "embedding": {
                "state": getattr(embedding, "state", None),
                "provider_type": getattr(embedding, "provider_type", ""),
                "model": getattr(embedding, "model", ""),
                "dimensions": getattr(embedding, "dimensions", None),
                "error": getattr(embedding, "error", None),
                "has_vector": getattr(embedding, "vector", None) is not None,
            },
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def archive_memory_item(item_id: int, *, user) -> dict[str, Any]:
    def _impl():
        item = MemoryItem.objects.filter(user=user, id=item_id).first()
        if not item:
            return {"error": "not_found"}
        item.status = MemoryItemStatus.ARCHIVED
        item.save(update_fields=["status", "updated_at"])
        return {"id": item.id, "status": item.status}

    return await sync_to_async(_impl, thread_sensitive=True)()


async def search_memory_items(
    *,
    query: str,
    user,
    limit: int = 10,
    theme: Optional[str] = None,
    types: Optional[list[str]] = None,
    recency_days: Optional[int] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    query = (query or "").strip()
    match_all = (query == "" or query == "*")

    try:
        limit = int(limit)
    except Exception as exc:
        raise ValidationError("limit must be an integer") from exc
    limit = max(1, min(limit, 50))

    query_vec = await resolve_query_vector(user_id=user.id, query=query)

    def _impl(vec: Optional[list[float]]):
        qs = MemoryItem.objects.select_related("theme").filter(user=user)

        status_value = (status or "").strip().lower() or MemoryItemStatus.ACTIVE
        if status_value != "any":
            valid_statuses = set(MemoryItemStatus.values)
            if status_value in valid_statuses:
                qs = qs.filter(status=status_value)
            else:
                qs = qs.filter(status=MemoryItemStatus.ACTIVE)

        if theme:
            qs = qs.filter(theme__slug=normalize_memory_theme_slug(theme))

        if types:
            valid_types = set(MemoryItemType.values)
            requested = [value for value in types if value in valid_types]
            if requested:
                qs = qs.filter(type__in=requested)

        if recency_days is not None:
            cutoff = timezone.now() - timedelta(days=int(recency_days))
            qs = qs.filter(created_at__gte=cutoff)

        results: list[dict[str, Any]] = []
        engine = connection.vendor

        if match_all:
            recent = qs.order_by(F("created_at").desc())
            for item in recent[:limit]:
                results.append(
                    {
                        "id": item.id,
                        "path": ensure_memory_virtual_path(item),
                        "theme": item.theme.slug if item.theme else None,
                        "type": item.type,
                        "content_snippet": item.content[:240],
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "score": None,
                        "signals": {"fts": False, "semantic": False},
                    }
                )
            return {
                "results": results,
                "notes": ["match-all mode: empty query or '*' returns most recent items"],
            }

        if engine == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
            from pgvector.django import CosineDistance

            vector = SearchVector("content", config="english")
            search_query = SearchQuery(query)
            candidate_limit = 50

            fts_qs = (
                qs.annotate(fts_rank=SearchRank(vector, search_query))
                .filter(fts_rank__gt=0.0)
                .order_by(F("fts_rank").desc(), F("created_at").desc())
            )
            fts_ids = list(fts_qs.values_list("id", flat=True)[:candidate_limit])

            semantic_ids: list[int] = []
            if vec is not None:
                semantic_qs = (
                    qs.filter(embedding__state="ready")
                    .annotate(distance=CosineDistance("embedding__vector", vec))
                    .order_by(F("distance").asc(), F("created_at").desc())
                )
                semantic_ids = list(semantic_qs.values_list("id", flat=True)[:candidate_limit])

            candidate_ids = list(dict.fromkeys([*semantic_ids, *fts_ids]))
            if not candidate_ids:
                return {"results": [], "notes": ["no matches"]}

            candidate_qs = (
                qs.filter(id__in=candidate_ids)
                .select_related("theme")
                .select_related("embedding")
                .annotate(fts_rank=SearchRank(vector, search_query))
            )
            if vec is not None:
                candidate_qs = candidate_qs.annotate(distance=CosineDistance("embedding__vector", vec))
            else:
                candidate_qs = candidate_qs.annotate(distance=F("id") * 0.0)

            candidates = list(candidate_qs)

            def _semantic_sim(item) -> Optional[float]:
                if vec is None:
                    return None
                distance = getattr(item, "distance", None)
                if distance is None:
                    return None
                return semantic_similarity_from_distance(distance, enabled=True)

            def _fts_score(item) -> float:
                return float(getattr(item, "fts_rank", 0.0) or 0.0)

            semantic_values = [value for value in (_semantic_sim(item) for item in candidates) if value is not None]
            fts_values = [_fts_score(item) for item in candidates]
            sem_min, sem_max = minmax_bounds(semantic_values)
            fts_min, fts_max = minmax_bounds(fts_values)

            scored: list[dict[str, Any]] = []
            for item in candidates:
                semantic_value = _semantic_sim(item)
                semantic_norm = minmax_normalize(semantic_value, vmin=sem_min, vmax=sem_max) if semantic_value is not None else 0.0
                fts_value = _fts_score(item)
                fts_norm = minmax_normalize(fts_value, vmin=fts_min, vmax=fts_max)
                final_score = blend_semantic_fts(semantic=semantic_norm, fts=fts_norm)
                cosine_distance = None
                if vec is not None and getattr(item, "distance", None) is not None:
                    cosine_distance = float(getattr(item, "distance", 0.0))
                scored.append(
                    {
                        "item": item,
                        "final_score": final_score,
                        "fts_rank": fts_value,
                        "cosine_distance": cosine_distance,
                    }
                )

            scored.sort(
                key=lambda row: (
                    -row["final_score"],
                    -(row["item"].created_at.timestamp() if getattr(row["item"], "created_at", None) else 0.0),
                    row["item"].id,
                )
            )

            for row in scored[:limit]:
                item = row["item"]
                results.append(
                    {
                        "id": item.id,
                        "path": ensure_memory_virtual_path(item),
                        "theme": item.theme.slug if item.theme else None,
                        "type": item.type,
                        "content_snippet": item.content[:240],
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "score": {
                            "final": float(row["final_score"]),
                            "fts_rank": float(row["fts_rank"]),
                            "cosine_distance": row["cosine_distance"],
                        },
                        "signals": {"fts": True, "semantic": vec is not None},
                    }
                )
        else:
            filtered = qs.filter(Q(content__icontains=query)).order_by(F("created_at").desc())
            for item in filtered[:limit]:
                results.append(
                    {
                        "id": item.id,
                        "path": ensure_memory_virtual_path(item),
                        "theme": item.theme.slug if item.theme else None,
                        "type": item.type,
                        "content_snippet": item.content[:240],
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "score": None,
                        "signals": {"fts": True, "semantic": False},
                    }
                )

        return {
            "results": results,
            "notes": [
                "semantic ranking is enabled only when embeddings provider is configured and vectors are ready",
            ],
        }

    return await sync_to_async(_impl, thread_sensitive=True)(query_vec)


async def get_memory_item_by_path(*, user, path: str, include_archived: bool = False) -> MemoryItem | None:
    spec = parse_memory_virtual_path(path)
    if spec.kind != "item":
        return None

    def _impl():
        queryset = MemoryItem.objects.select_related("theme").filter(
            user=user,
            virtual_path=spec.normalized_path,
        )
        if not include_archived:
            queryset = queryset.filter(status=MemoryItemStatus.ACTIVE)
        return queryset.order_by("-updated_at", "-id").first()

    return await sync_to_async(_impl, thread_sensitive=True)()


async def read_memory_document(*, user, path: str) -> MemoryVirtualEntry:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "readme":
        return MemoryVirtualEntry(
            path=MEMORY_README_PATH,
            mime_type="text/markdown",
            size=len(MEMORY_README_CONTENT.encode("utf-8")),
        )
    if spec.kind != "item":
        raise ValidationError("Memory path does not reference a file")

    item = await get_memory_item_by_path(user=user, path=spec.normalized_path, include_archived=False)
    if item is None:
        raise ValidationError("Memory item not found")
    rendered = render_memory_document(item)
    mime_type = "text/plain" if spec.extension == ".txt" else "text/markdown"
    return MemoryVirtualEntry(
        path=spec.normalized_path,
        mime_type=mime_type,
        size=len(rendered.encode("utf-8")),
        item_id=item.id,
    )


async def read_memory_text(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "readme":
        return MEMORY_README_CONTENT
    item = await get_memory_item_by_path(user=user, path=spec.normalized_path, include_archived=False)
    if item is None:
        raise ValidationError("Memory item not found")
    return render_memory_document(item)


async def list_memory_dir_entries(*, user, path: str) -> list[dict[str, Any]]:
    spec = parse_memory_virtual_path(path)

    def _root_entries():
        entries = [
            {"name": "README.md", "path": MEMORY_README_PATH, "type": "file", "mime_type": "text/markdown", "size": len(MEMORY_README_CONTENT.encode("utf-8"))},
        ]
        for theme in MemoryTheme.objects.filter(user=user).order_by("slug"):
            entries.append({"name": theme.slug, "path": f"{MEMORY_ROOT}/{theme.slug}", "type": "dir"})
        return entries

    def _theme_entries():
        queryset = (
            MemoryItem.objects.select_related("theme")
            .filter(user=user, status=MemoryItemStatus.ACTIVE, theme__slug=spec.theme_slug)
            .order_by("virtual_path", "id")
        )
        entries = []
        for item in queryset:
            item_path = ensure_memory_virtual_path(item)
            mime_type = "text/plain" if item_path.endswith(".txt") else "text/markdown"
            rendered = render_memory_document(item)
            entries.append(
                {
                    "name": posixpath.basename(item_path),
                    "path": item_path,
                    "type": "file",
                    "mime_type": mime_type,
                    "size": len(rendered.encode("utf-8")),
                }
            )
        return entries

    if spec.kind == "root":
        return await sync_to_async(_root_entries, thread_sensitive=True)()
    if spec.kind == "theme_dir":
        return await sync_to_async(_theme_entries, thread_sensitive=True)()
    raise ValidationError("Memory path does not reference a directory")


async def memory_path_exists(*, user, path: str) -> bool:
    try:
        spec = parse_memory_virtual_path(path)
    except ValidationError:
        return False

    if spec.kind in {"root", "readme"}:
        return True
    if spec.kind == "theme_dir":
        def _theme_exists():
            return MemoryTheme.objects.filter(user=user, slug=spec.theme_slug).exists()

        return await sync_to_async(_theme_exists, thread_sensitive=True)()
    return await get_memory_item_by_path(user=user, path=spec.normalized_path, include_archived=False) is not None


async def memory_is_dir(*, user, path: str) -> bool:
    try:
        spec = parse_memory_virtual_path(path)
    except ValidationError:
        return False
    if spec.kind in {"root", "theme_dir"}:
        if spec.kind == "theme_dir":
            return await memory_path_exists(user=user, path=spec.normalized_path)
        return True
    return False


async def mkdir_memory_theme(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "root":
        return MEMORY_ROOT
    if spec.kind != "theme_dir":
        raise ValidationError("mkdir only supports /memory/<theme>")

    def _impl():
        _get_or_create_theme(user=user, theme_slug=spec.theme_slug or get_default_memory_theme_slug())
        return spec.normalized_path

    return await sync_to_async(_impl, thread_sensitive=True)()


async def write_memory_document(
    *,
    user,
    path: str,
    text: str,
    source_thread=None,
    source_message=None,
) -> MemoryVirtualEntry:
    spec = parse_memory_virtual_path(path)
    if spec.kind != "item":
        raise ValidationError("Memory writes must target /memory/<theme>/<file>.md or .txt")

    editable_fields, body = _editable_fields_from_document(text)
    embeddings_enabled = await aget_embeddings_provider(user_id=user.id) is not None

    def _impl():
        theme_obj = _get_or_create_theme(user=user, theme_slug=spec.theme_slug or get_default_memory_theme_slug())
        item = (
            MemoryItem.objects.select_related("theme")
            .filter(user=user, virtual_path=spec.normalized_path, status=MemoryItemStatus.ACTIVE)
            .first()
        )
        if item is None:
            item_type = editable_fields.get("type", MemoryItemType.OTHER)
            item_tags = editable_fields.get("tags", [])
            item = MemoryItem.objects.create(
                user=user,
                theme=theme_obj,
                type=item_type,
                content=body,
                tags=item_tags,
                source_thread=source_thread,
                source_message=source_message,
                virtual_path=spec.normalized_path,
            )
        else:
            item.theme = theme_obj
            item.virtual_path = spec.normalized_path
            item.content = body
            if "type" in editable_fields:
                item.type = editable_fields["type"]
            if "tags" in editable_fields:
                item.tags = editable_fields["tags"]
            item.save(update_fields=["theme", "virtual_path", "content", "type", "tags", "updated_at"])

        embedding_state = _ensure_embedding_state(item, embeddings_enabled=embeddings_enabled)
        rendered = render_memory_document(item)
        mime_type = "text/plain" if spec.extension == ".txt" else "text/markdown"
        return MemoryVirtualEntry(
            path=spec.normalized_path,
            mime_type=mime_type,
            size=len(rendered.encode("utf-8")),
            item_id=item.id,
        ), embedding_state

    entry, _embedding_state = await sync_to_async(_impl, thread_sensitive=True)()
    return entry


async def move_memory_path(*, user, source_path: str, destination_path: str) -> str:
    source_spec = parse_memory_virtual_path(source_path)
    destination_spec = parse_memory_virtual_path(destination_path)
    if source_spec.kind != "item" or destination_spec.kind != "item":
        raise ValidationError("Memory moves must target memory files")

    def _impl():
        item = (
            MemoryItem.objects.select_related("theme")
            .filter(user=user, virtual_path=source_spec.normalized_path, status=MemoryItemStatus.ACTIVE)
            .first()
        )
        if item is None:
            raise ValidationError("Memory item not found")
        if source_spec.normalized_path == destination_spec.normalized_path:
            return destination_spec.normalized_path
        if MemoryItem.objects.filter(
            user=user,
            virtual_path=destination_spec.normalized_path,
            status=MemoryItemStatus.ACTIVE,
        ).exclude(id=item.id).exists():
            raise ValidationError("A memory item already exists at the destination path")

        item.theme = _get_or_create_theme(user=user, theme_slug=destination_spec.theme_slug or get_default_memory_theme_slug())
        item.virtual_path = destination_spec.normalized_path
        item.save(update_fields=["theme", "virtual_path", "updated_at"])
        return destination_spec.normalized_path

    return await sync_to_async(_impl, thread_sensitive=True)()


async def archive_memory_path(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind != "item":
        raise ValidationError("Only memory files can be archived")

    def _impl():
        item = (
            MemoryItem.objects.filter(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryItemStatus.ACTIVE,
            )
            .first()
        )
        if item is None:
            raise ValidationError("Memory item not found")
        item.status = MemoryItemStatus.ARCHIVED
        item.save(update_fields=["status", "updated_at"])
        return spec.normalized_path

    return await sync_to_async(_impl, thread_sensitive=True)()


async def find_memory_paths(*, user, start_path: str, term: str = "") -> list[str]:
    spec = parse_memory_virtual_path(start_path)
    lowered_term = str(term or "").lower()

    def _impl():
        matches: list[str] = []
        if spec.kind == "root":
            if not lowered_term or "readme.md".find(lowered_term) >= 0:
                matches.append(MEMORY_README_PATH)
            for theme in MemoryTheme.objects.filter(user=user).order_by("slug"):
                theme_path = f"{MEMORY_ROOT}/{theme.slug}"
                if not lowered_term or theme.slug.lower().find(lowered_term) >= 0:
                    matches.append(theme_path)
            queryset = MemoryItem.objects.filter(user=user, status=MemoryItemStatus.ACTIVE).order_by("virtual_path", "id")
            for item in queryset:
                path_value = ensure_memory_virtual_path(item)
                basename = posixpath.basename(path_value).lower()
                if not lowered_term or lowered_term in basename:
                    matches.append(path_value)
            return sorted(set(matches))

        if spec.kind == "theme_dir":
            matches.append(spec.normalized_path)
            queryset = MemoryItem.objects.filter(
                user=user,
                status=MemoryItemStatus.ACTIVE,
                theme__slug=spec.theme_slug,
            ).order_by("virtual_path", "id")
            for item in queryset:
                path_value = ensure_memory_virtual_path(item)
                basename = posixpath.basename(path_value).lower()
                if not lowered_term or lowered_term in basename:
                    matches.append(path_value)
            return sorted(set(matches))

        if spec.kind == "readme":
            if not lowered_term or "readme.md".find(lowered_term) >= 0:
                return [MEMORY_README_PATH]
            return []

        item = (
            MemoryItem.objects.filter(
                user=user,
                status=MemoryItemStatus.ACTIVE,
                virtual_path=spec.normalized_path,
            )
            .first()
        )
        if item is None:
            return []
        basename = posixpath.basename(spec.normalized_path).lower()
        if lowered_term and lowered_term not in basename:
            return []
        return [spec.normalized_path]

    return await sync_to_async(_impl, thread_sensitive=True)()
