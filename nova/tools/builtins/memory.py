"""Nova builtin tool: Memory (v2)

This replaces the original theme/markdown-based memory with a structured store.

Agent-facing functions exposed:
- `search(query, limit=10, theme=None, types=None, recency_days=None)`
- `add(type, content, theme=None, tags=None)`
- `get(item_id)`
- `list_themes()`

Notes:
- Hybrid search (FTS + pgvector) is implemented as FTS-first for now.
- Semantic scoring will be enabled once query-embedding computation is wired
  (Celery task + provider selection). Until then, stored item embeddings are
  kept but not used for ranking.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F, Q
from django.utils import timezone
from langchain_core.tools import StructuredTool

from nova.llm.hybrid_search import (
    blend_semantic_fts,
    minmax_bounds,
    minmax_normalize,
    resolve_query_vector,
    semantic_similarity_from_distance,
)
from nova.llm.llm_agent import LLMAgent
from nova.llm.embeddings import aget_embeddings_provider
from nova.models.Memory import MemoryItem, MemoryItemEmbedding, MemoryItemStatus, MemoryItemType, MemoryTheme

METADATA = {
    'name': 'Memory',
    'description': 'Access and manage structured long-term memory (search + add + get).',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


def get_prompt_instructions() -> List[str]:
    """Tool-owned prompt guidance for long-term memory usage."""
    return [
        "Use memory_search when you need user-specific facts/preferences not guaranteed in current context.",
        "Use memory_get to read a specific memory item in full before relying on it.",
        "Use memory_add for durable user preferences/facts that should persist across conversations.",
    ]


def _normalize_theme_slug(theme: str) -> str:
    theme = (theme or "").strip().lower()
    if not theme:
        raise ValidationError("theme must be a non-empty string")
    return theme.replace(" ", "-")


def _get_default_theme_slug() -> str:
    return "general"


async def list_themes(agent: LLMAgent, status: Optional[str] = None) -> Dict[str, Any]:
    """List themes available in the structured memory store.

    Default behavior mirrors `search`: only themes with active items are returned,
    unless explicitly overridden via `status`.
    """

    def _impl():
        status_value = (status or "").strip().lower() or MemoryItemStatus.ACTIVE
        valid_statuses = set(MemoryItemStatus.values)

        item_filter = Q(items__user=agent.user)
        if status_value != "any":
            if status_value not in valid_statuses:
                status_value = MemoryItemStatus.ACTIVE
            item_filter &= Q(items__status=status_value)

        themes = (
            MemoryTheme.objects.filter(user=agent.user)
            .filter(item_filter)
            .distinct()
            .order_by("slug")
        )
        return {
            "themes": [
                {
                    "slug": t.slug,
                    "display_name": t.display_name,
                    "description": t.description,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in themes
            ]
        }

    # NOTE: SQLite (tests) is prone to "database table is locked" errors when ORM
    # runs in a separate worker thread. Keep DB access thread-sensitive.
    return await sync_to_async(_impl, thread_sensitive=True)()


async def add(
    type: str,
    content: str,
    agent: LLMAgent,
    theme: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new MemoryItem. Embedding will be computed asynchronously later."""

    provider = await aget_embeddings_provider(user_id=agent.user.id)
    embeddings_enabled = provider is not None

    def _impl():
        item_type = type
        if item_type not in set(MemoryItemType.values):
            raise ValidationError(f"Invalid memory item type: {item_type}")

        if not content or not content.strip():
            raise ValidationError("content must be a non-empty string")

        # Theme is optional from the agent perspective, but we avoid storing
        # "theme-less" items to keep enumeration/retrieval predictable.
        theme_value = (theme or "").strip()
        if not theme_value:
            theme_value = _get_default_theme_slug()

        slug = _normalize_theme_slug(theme_value)
        theme_obj, _ = MemoryTheme.objects.get_or_create(
            user=agent.user,
            slug=slug,
            defaults={"display_name": theme_value},
        )

        item = MemoryItem.objects.create(
            user=agent.user,
            theme=theme_obj,
            type=item_type,
            content=content.strip(),
            tags=[t for t in (tags or []) if isinstance(t, str) and t.strip()],
            source_thread=getattr(agent, "thread", None),
        )

        # Create embedding row only when embeddings are enabled.
        embedding_state = None
        if embeddings_enabled:
            emb, _ = MemoryItemEmbedding.objects.get_or_create(
                user=agent.user,
                item=item,
                defaults={
                    "state": "pending",
                    "dimensions": 1024,
                },
            )
            embedding_state = emb.state

            # Enqueue embedding computation (best-effort). We keep tool call fast.
            try:
                from nova.tasks.memory_tasks import compute_memory_item_embedding_task

                compute_memory_item_embedding_task.delay(emb.id)
            except Exception:
                # If Celery is not running, keep the embedding in pending state.
                pass

        return {
            "id": item.id,
            "embedding_state": embedding_state,
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def get(item_id: int, agent: LLMAgent) -> Dict[str, Any]:
    """Fetch a memory item by id."""

    def _impl():
        item = (
            MemoryItem.objects.select_related("theme")
            .filter(user=agent.user, id=item_id)
            .first()
        )
        if not item:
            return {"error": "not_found"}

        embedding = getattr(item, "embedding", None)
        return {
            "id": item.id,
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


async def archive(item_id: int, agent: LLMAgent) -> Dict[str, Any]:
    """Archive a memory item (soft delete).

    This is intentionally preferred to hard delete to preserve traceability.
    """

    def _impl():
        item = MemoryItem.objects.filter(user=agent.user, id=item_id).first()
        if not item:
            return {"error": "not_found"}

        # Keep it simple: archive the item. We keep the row for audit/debug.
        item.status = MemoryItemStatus.ARCHIVED
        item.save(update_fields=["status", "updated_at"])

        return {
            "id": item.id,
            "status": item.status,
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def search(
    query: str,
    agent: LLMAgent,
    limit: int = 10,
    theme: Optional[str] = None,
    types: Optional[List[str]] = None,
    recency_days: Optional[int] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Search memory items.

    Current implementation:
    - Uses PostgreSQL FTS when available.
    - Falls back to icontains for non-Postgres DBs (e.g. SQLite tests).
    - pgvector semantic ranking will be enabled later once query embeddings exist.
    """

    # Match-all support: empty query or '*' returns most recent items.
    query = (query or "").strip()
    match_all = (query == "" or query == "*")

    try:
        limit = int(limit)
    except Exception as e:
        raise ValidationError("limit must be an integer") from e
    limit = max(1, min(limit, 50))

    query_vec = await resolve_query_vector(user_id=agent.user.id, query=query)

    def _impl(vec: Optional[List[float]]):
        qs = MemoryItem.objects.select_related("theme").filter(user=agent.user)

        # Default to ACTIVE items only unless explicitly overridden.
        status_value = (status or "").strip().lower()
        if not status_value:
            status_value = MemoryItemStatus.ACTIVE

        if status_value != "any":
            valid_statuses = set(MemoryItemStatus.values)
            if status_value in valid_statuses:
                qs = qs.filter(status=status_value)
            else:
                # Unknown status: fall back to default ACTIVE.
                qs = qs.filter(status=MemoryItemStatus.ACTIVE)

        if theme:
            slug = _normalize_theme_slug(theme)
            qs = qs.filter(theme__slug=slug)

        if types:
            valid_types = set(MemoryItemType.values)
            requested = [t for t in types if t in valid_types]
            if requested:
                qs = qs.filter(type__in=requested)

        if recency_days is not None:
            cutoff = timezone.now() - timedelta(days=int(recency_days))
            qs = qs.filter(created_at__gte=cutoff)

        engine = connection.vendor

        results: List[Dict[str, Any]] = []

        if match_all:
            # No lexical filter: just return the newest items.
            qs_recent = qs.order_by(F("created_at").desc())
            for item in qs_recent[:limit]:
                results.append(
                    {
                        "id": item.id,
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
                "notes": [
                    "match-all mode: empty query or '*' returns most recent items",
                ],
            }

        if engine == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
            from pgvector.django import CosineDistance
            # ------------------------------------------------------------
            # Hybrid retrieval strategy (v1): 70% semantic / 30% FTS
            #
            # Implementation approach:
            # 1) Fetch top-K candidates from semantic and lexical signals.
            # 2) Union candidate IDs.
            # 3) Compute per-item signals and do a small in-Python rerank.
            #
            # This keeps DB queries simple and avoids having to implement
            # complex SQL normalization functions.
            # ------------------------------------------------------------
            K = 50

            vector = SearchVector("content", config="english")
            q = SearchQuery(query)

            # Lexical (FTS) candidates
            fts_qs = (
                qs.annotate(fts_rank=SearchRank(vector, q))
                .filter(fts_rank__gt=0.0)
                .order_by(F("fts_rank").desc(), F("created_at").desc())
            )
            fts_ids = list(fts_qs.values_list("id", flat=True)[:K])

            # Semantic candidates (only if a query vector exists)
            semantic_ids: List[int] = []
            if vec is not None:
                semantic_qs = (
                    qs.filter(embedding__state="ready")
                    .annotate(distance=CosineDistance("embedding__vector", vec))
                    .order_by(F("distance").asc(), F("created_at").desc())
                )
                semantic_ids = list(semantic_qs.values_list("id", flat=True)[:K])

            candidate_ids = list(dict.fromkeys([*semantic_ids, *fts_ids]))
            if not candidate_ids:
                return {
                    "results": [],
                    "notes": [
                        "no matches",
                    ],
                }

            # Load candidates with both signals (where possible)
            candidates_qs = (
                qs.filter(id__in=candidate_ids)
                .select_related("theme")
                .select_related("embedding")
                .annotate(fts_rank=SearchRank(vector, q))
            )

            if vec is not None:
                candidates_qs = candidates_qs.annotate(
                    distance=CosineDistance("embedding__vector", vec)
                )
            else:
                candidates_qs = candidates_qs.annotate(
                    distance=F("id") * 0.0  # dummy numeric column
                )

            candidates = list(candidates_qs)

            # Compute normalized scores
            def _semantic_sim(item) -> Optional[float]:
                dist = getattr(item, "distance", None)
                # Distance only meaningful when embedding exists and query vec exists.
                if vec is None:
                    return None
                if dist is None:
                    return None
                return semantic_similarity_from_distance(dist, enabled=True)

            def _fts_score(item) -> float:
                return float(getattr(item, "fts_rank", 0.0) or 0.0)

            semantic_vals = [v for v in (_semantic_sim(i) for i in candidates) if v is not None]
            fts_vals = [_fts_score(i) for i in candidates]

            sem_min, sem_max = minmax_bounds(semantic_vals)
            fts_min, fts_max = minmax_bounds(fts_vals)

            scored: List[Dict[str, Any]] = []
            for item in candidates:
                sem = _semantic_sim(item)
                sem_norm = minmax_normalize(sem, vmin=sem_min, vmax=sem_max) if sem is not None else 0.0
                fts = _fts_score(item)
                fts_norm = minmax_normalize(fts, vmin=fts_min, vmax=fts_max)
                final_score = blend_semantic_fts(semantic=sem_norm, fts=fts_norm)
                cosine_distance = None
                if vec is not None and getattr(item, "distance", None) is not None:
                    cosine_distance = float(getattr(item, "distance", 0.0))

                scored.append(
                    {
                        "item": item,
                        "final_score": final_score,
                        "fts_rank": fts,
                        "cosine_distance": cosine_distance,
                    }
                )

            scored.sort(
                key=lambda r: (
                    -r["final_score"],
                    -(r["item"].created_at.timestamp() if getattr(r["item"], "created_at", None) else 0.0),
                    r["item"].id,
                )
            )

            for row in scored[:limit]:
                item = row["item"]
                results.append(
                    {
                        "id": item.id,
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
            # SQLite (tests) or other DBs: degrade gracefully.
            qs2 = qs.filter(Q(content__icontains=query)).order_by(F("created_at").desc())
            for item in qs2[:limit]:
                results.append(
                    {
                        "id": item.id,
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


async def get_functions(tool, agent: LLMAgent):
    """
    Return a list of StructuredTool instances for the available functions.
    """
    return [
        StructuredTool.from_function(
            coroutine=lambda query, limit=10, theme=None, types=None, recency_days=None, status=None: search(
                query=query,
                limit=limit,
                theme=theme,
                types=types,
                recency_days=recency_days,
                status=status,
                agent=agent,
            ),
            name="memory_search",
            description="Search long-term memory items relevant to a query",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (empty or * will select all)"},
                    "limit": {"type": "integer", "description": "Max results (1-50)", "default": 10},
                    "theme": {"type": "string", "description": "Optional theme slug"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of memory item types",
                    },
                    "recency_days": {
                        "type": "integer",
                        "description": "Optional: only items from the last N days",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional: filter by status (active|archived|any)",
                    },
                },
                "required": ["query"],
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda type, content, theme=None, tags=None: add(
                type=type,
                content=content,
                theme=theme,
                tags=tags,
                agent=agent,
            ),
            name="memory_add",
            description="Add a long-term memory item",
            args_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Memory item type (preference|fact|instruction|summary|other)",
                    },
                    "content": {"type": "string", "description": "Memory content"},
                    "theme": {"type": "string", "description": "Optional theme"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags",
                    },
                },
                "required": ["type", "content"],
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda item_id: get(item_id, agent),
            name="memory_get",
            description="Get a memory item by id",
            args_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Memory item id"},
                },
                "required": ["item_id"],
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda status=None: list_themes(agent=agent, status=status),
            name="memory_list_themes",
            description="List themes in long-term memory",
            args_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional: filter by associated item status (active|archived|any)",
                    },
                },
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda item_id: archive(item_id=item_id, agent=agent),
            name="memory_archive",
            description="Archive (soft-delete) a memory item by id",
            args_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Memory item id"},
                },
                "required": ["item_id"],
            },
        ),
    ]
