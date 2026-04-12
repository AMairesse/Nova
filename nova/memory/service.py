from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify

from nova.llm.embeddings import aget_embeddings_provider
from nova.llm.hybrid_search import (
    blend_semantic_fts,
    minmax_bounds,
    minmax_normalize,
    resolve_query_vector,
    semantic_similarity_from_distance,
)
from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDirectory import MemoryDirectory
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import (
    MEMORY_EMBEDDING_DIMENSIONS,
    MemoryChunkEmbeddingState,
    MemoryRecordStatus,
)

MEMORY_ROOT = "/memory"
MEMORY_README_PATH = "/memory/README.md"
MEMORY_ALLOWED_EXTENSIONS = {".md"}
MAX_MEMORY_SEARCH_LIMIT = 50
MEMORY_CHUNK_MAX_WORDS = 280
MEMORY_CHUNK_OVERLAP_WORDS = 40
MEMORY_EMBEDDINGS_QUEUE_WARNING = (
    "Warning: memory embeddings remain pending because background calculation "
    "could not be queued immediately."
)

logger = logging.getLogger(__name__)

MEMORY_README_CONTENT = """# Memory

`/memory` is a user-scoped virtual filesystem shared across the current user's
Nova agents that have memory access.

Use it like this:
- `ls /memory`
- `mkdir /memory/projects`
- `touch /memory/projects/client-a.md`
- `tee /memory/projects/client-a.md --text "# Client A\\n\\n## Constraints\\n..."`
- `grep -r "deadline" /memory`
- `memory search "client deadline" --under /memory/projects`

Use `grep` for lexical text matching on visible files.
Use `memory search` for hybrid lexical + embeddings retrieval over memory chunks.
"""


@dataclass(slots=True, frozen=True)
class MemoryPathSpec:
    kind: str
    normalized_path: str
    parent_path: str | None = None
    basename: str | None = None


@dataclass(slots=True)
class MemoryVirtualEntry:
    path: str
    mime_type: str
    size: int
    document_id: int | None = None
    warnings: tuple[str, ...] = ()


def _normalize_memory_path(path: str) -> str:
    normalized = posixpath.normpath(str(path or "").strip() or MEMORY_ROOT)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def parse_memory_virtual_path(path: str) -> MemoryPathSpec:
    normalized = _normalize_memory_path(path)
    if normalized == MEMORY_ROOT:
        return MemoryPathSpec(kind="root", normalized_path=MEMORY_ROOT)
    if normalized == MEMORY_README_PATH:
        return MemoryPathSpec(
            kind="readme",
            normalized_path=MEMORY_README_PATH,
            parent_path=MEMORY_ROOT,
            basename="README.md",
        )
    if not normalized.startswith(f"{MEMORY_ROOT}/"):
        raise ValidationError("Not a memory path")

    basename = posixpath.basename(normalized)
    parent_path = posixpath.dirname(normalized)
    if basename == "README.md":
        raise ValidationError("README.md is reserved inside /memory")

    if posixpath.splitext(basename)[1].lower() in MEMORY_ALLOWED_EXTENSIONS:
        return MemoryPathSpec(
            kind="item",
            normalized_path=normalized,
            parent_path=parent_path,
            basename=basename,
        )

    if "." in basename:
        raise ValidationError("Memory files must use the .md extension")

    return MemoryPathSpec(
        kind="dir",
        normalized_path=normalized,
        parent_path=parent_path,
        basename=basename,
    )


def is_memory_path(path: str) -> bool:
    try:
        parse_memory_virtual_path(path)
        return True
    except ValidationError:
        return False


def _humanize_basename(path: str) -> str:
    basename = posixpath.basename(path).rsplit(".", 1)[0]
    return re.sub(r"[-_]+", " ", basename).strip().title() or "Memory"


def _normalize_markdown(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _extract_title(markdown: str, *, fallback_path: str) -> str:
    source = _normalize_markdown(markdown)
    for line in source.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return stripped[2:].strip()[:255] or _humanize_basename(fallback_path)
        break
    return _humanize_basename(fallback_path)


def _slug_from_heading(heading: str, fallback: str) -> str:
    value = slugify(str(heading or "").strip())
    return value or fallback


def _split_paragraphs(text: str) -> list[str]:
    source = _normalize_markdown(text).strip()
    if not source:
        return []
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", source) if chunk.strip()]


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _chunk_words(text: str, *, heading: str, anchor: str) -> list[dict[str, Any]]:
    words = str(text or "").split()
    if not words:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    index = 1
    while start < len(words):
        end = min(start + MEMORY_CHUNK_MAX_WORDS, len(words))
        chunk_text = " ".join(words[start:end]).strip()
        chunk_anchor = anchor if index == 1 else f"{anchor}-{index}"
        chunks.append(
            {
                "heading": heading,
                "anchor": chunk_anchor,
                "content_text": chunk_text,
                "token_count": _word_count(chunk_text),
            }
        )
        if end >= len(words):
            break
        start = max(end - MEMORY_CHUNK_OVERLAP_WORDS, start + 1)
        index += 1
    return chunks


def _chunk_section_text(*, heading: str, anchor: str, text: str) -> list[dict[str, Any]]:
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    emitted: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_words = 0
    anchor_index = 1

    def _flush() -> None:
        nonlocal buffer, buffer_words, anchor_index
        if not buffer:
            return
        chunk_text = "\n\n".join(buffer).strip()
        chunk_anchor = anchor if anchor_index == 1 else f"{anchor}-{anchor_index}"
        emitted.append(
            {
                "heading": heading,
                "anchor": chunk_anchor,
                "content_text": chunk_text,
                "token_count": _word_count(chunk_text),
            }
        )
        buffer = []
        buffer_words = 0
        anchor_index += 1

    for paragraph in paragraphs:
        paragraph_words = _word_count(paragraph)
        if paragraph_words > MEMORY_CHUNK_MAX_WORDS:
            _flush()
            for chunk in _chunk_words(paragraph, heading=heading, anchor=f"{anchor}-{anchor_index}"):
                emitted.append(chunk)
                anchor_index += 1
            continue
        if buffer and buffer_words + paragraph_words > MEMORY_CHUNK_MAX_WORDS:
            _flush()
        buffer.append(paragraph)
        buffer_words += paragraph_words

    _flush()
    return emitted


def _parse_markdown_sections(markdown: str, *, path: str) -> tuple[str, list[dict[str, Any]]]:
    source = _normalize_markdown(markdown).strip()
    title = _extract_title(source, fallback_path=path)
    lines = source.split("\n") if source else []

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].strip().startswith("# "):
        index += 1

    sections: list[tuple[str, list[str]]] = []
    current_heading = title
    current_lines: list[str] = []

    for line in lines[index:]:
        heading_match = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if heading_match:
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = heading_match.group(1).strip() or title
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        sections.append((current_heading, current_lines))

    if not sections and source:
        sections = [(title, lines[index:])]

    chunk_specs: list[dict[str, Any]] = []
    position = 0
    for heading, section_lines in sections:
        section_text = "\n".join(section_lines).strip()
        anchor = _slug_from_heading(heading, fallback=f"section-{position + 1}")
        for chunk in _chunk_section_text(heading=heading, anchor=anchor, text=section_text):
            chunk["position"] = position
            chunk_specs.append(chunk)
            position += 1

    return title, chunk_specs


def _iter_parent_directories(path: str) -> list[str]:
    normalized = _normalize_memory_path(path)
    parents: list[str] = []
    current = posixpath.dirname(normalized)
    while current.startswith(f"{MEMORY_ROOT}/") or current == MEMORY_ROOT:
        parents.append(current)
        if current == MEMORY_ROOT:
            break
        current = posixpath.dirname(current)
    parents.reverse()
    return parents


def _load_active_paths(*, user) -> tuple[list[str], list[tuple[str, int]]]:
    directories = list(
        MemoryDirectory.objects.filter(
            user=user,
            status=MemoryRecordStatus.ACTIVE,
        ).values_list("virtual_path", flat=True)
    )
    documents = list(
        MemoryDocument.objects.filter(
            user=user,
            status=MemoryRecordStatus.ACTIVE,
        ).values_list("virtual_path", "id")
    )
    return directories, documents


def _collect_existing_directories(*, directories: list[str], document_paths: list[str]) -> set[str]:
    existing = {MEMORY_ROOT}
    for directory in directories:
        for parent in _iter_parent_directories(directory):
            existing.add(parent)
        existing.add(directory)
    for document_path in document_paths:
        for parent in _iter_parent_directories(document_path):
            existing.add(parent)
    return existing


def _memory_dir_has_children(*, user, path: str) -> bool:
    prefix = path.rstrip("/") + "/"
    return (
        MemoryDirectory.objects.filter(
            user=user,
            status=MemoryRecordStatus.ACTIVE,
            virtual_path__startswith=prefix,
        ).exists()
        or MemoryDocument.objects.filter(
            user=user,
            status=MemoryRecordStatus.ACTIVE,
            virtual_path__startswith=prefix,
        ).exists()
    )


def _schedule_chunk_embeddings(*, user_id: int, document_path: str, chunk_ids: list[int]) -> tuple[str, ...]:
    if not chunk_ids:
        return ()
    try:
        from nova.tasks.memory_tasks import compute_memory_chunk_embedding_task
    except Exception:
        logger.warning(
            "[memory_embedding_enqueue_failed] user=%s document=%s chunk_ids=%s",
            user_id,
            document_path,
            chunk_ids,
            exc_info=True,
        )
        return (MEMORY_EMBEDDINGS_QUEUE_WARNING,)

    failed_chunk_ids: list[int] = []
    for chunk_id in chunk_ids:
        try:
            compute_memory_chunk_embedding_task.delay(chunk_id)
        except Exception:
            failed_chunk_ids.append(chunk_id)
            logger.warning(
                "[memory_embedding_enqueue_failed] user=%s document=%s chunk_id=%s",
                user_id,
                document_path,
                chunk_id,
                exc_info=True,
            )

    if failed_chunk_ids:
        return (MEMORY_EMBEDDINGS_QUEUE_WARNING,)
    return ()


def _rebuild_document_chunks(*, document: MemoryDocument) -> list[int]:
    MemoryChunk.objects.filter(
        document=document,
        status=MemoryRecordStatus.ACTIVE,
    ).update(status=MemoryRecordStatus.ARCHIVED, updated_at=timezone.now())

    _title, chunk_specs = _parse_markdown_sections(document.content_markdown, path=document.virtual_path)
    created_chunk_ids: list[int] = []
    for chunk_spec in chunk_specs:
        chunk = MemoryChunk.objects.create(
            document=document,
            heading=chunk_spec["heading"],
            anchor=chunk_spec["anchor"],
            position=chunk_spec["position"],
            content_text=chunk_spec["content_text"],
            token_count=chunk_spec["token_count"],
            status=MemoryRecordStatus.ACTIVE,
        )
        MemoryChunkEmbedding.objects.create(
            chunk=chunk,
            state=MemoryChunkEmbeddingState.PENDING,
            dimensions=MEMORY_EMBEDDING_DIMENSIONS,
        )
        created_chunk_ids.append(chunk.id)
    return created_chunk_ids


async def list_memory_documents_overview(*, user, include_archived: bool = False, q: str = "") -> list[dict[str, Any]]:
    def _impl():
        queryset = MemoryDocument.objects.filter(user=user)
        if not include_archived:
            queryset = queryset.filter(status=MemoryRecordStatus.ACTIVE)
        if q:
            queryset = queryset.filter(
                Q(virtual_path__icontains=q) | Q(content_markdown__icontains=q)
            )
        queryset = queryset.order_by("-updated_at", "virtual_path").prefetch_related("chunks__embedding")

        rows: list[dict[str, Any]] = []
        for document in queryset:
            active_chunks = [
                chunk for chunk in list(document.chunks.all())
                if chunk.status == MemoryRecordStatus.ACTIVE
            ]
            ready = sum(1 for chunk in active_chunks if getattr(getattr(chunk, "embedding", None), "state", None) == MemoryChunkEmbeddingState.READY)
            pending = sum(1 for chunk in active_chunks if getattr(getattr(chunk, "embedding", None), "state", None) == MemoryChunkEmbeddingState.PENDING)
            errored = sum(1 for chunk in active_chunks if getattr(getattr(chunk, "embedding", None), "state", None) == MemoryChunkEmbeddingState.ERROR)
            rows.append(
                {
                    "document": document,
                    "chunk_count": len(active_chunks),
                    "ready_embeddings": ready,
                    "pending_embeddings": pending,
                    "error_embeddings": errored,
                }
            )
        return rows

    return await sync_to_async(_impl, thread_sensitive=True)()


async def memory_document_has_content(*, user, path: str) -> bool:
    spec = parse_memory_virtual_path(path)
    if spec.kind != "item":
        return False

    def _impl():
        document = (
            MemoryDocument.objects.filter(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        return bool(document and str(document.content_markdown or "").strip())

    return await sync_to_async(_impl, thread_sensitive=True)()


async def count_memory_chunk_embeddings(*, user) -> int:
    def _impl():
        return MemoryChunkEmbedding.objects.filter(
            chunk__document__user=user,
            chunk__document__status=MemoryRecordStatus.ACTIVE,
            chunk__status=MemoryRecordStatus.ACTIVE,
        ).count()

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

    def _impl():
        document = (
            MemoryDocument.objects.filter(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        if document is None:
            raise ValidationError("Memory document not found")
        content = str(document.content_markdown or "")
        return MemoryVirtualEntry(
            path=spec.normalized_path,
            mime_type="text/markdown",
            size=len(content.encode("utf-8")),
            document_id=document.id,
        )

    return await sync_to_async(_impl, thread_sensitive=True)()


async def read_memory_text(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "readme":
        return MEMORY_README_CONTENT
    if spec.kind != "item":
        raise ValidationError("Memory path does not reference a file")

    def _impl():
        document = (
            MemoryDocument.objects.filter(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        if document is None:
            raise ValidationError("Memory document not found")
        return str(document.content_markdown or "")

    return await sync_to_async(_impl, thread_sensitive=True)()


async def list_memory_dir_entries(*, user, path: str) -> list[dict[str, Any]]:
    spec = parse_memory_virtual_path(path)
    if spec.kind not in {"root", "dir"}:
        raise ValidationError("Memory path does not reference a directory")

    def _impl():
        directories, documents = _load_active_paths(user=user)
        document_paths = [path_value for path_value, _doc_id in documents]
        existing_dirs = _collect_existing_directories(
            directories=directories,
            document_paths=document_paths,
        )
        if spec.normalized_path not in existing_dirs:
            raise ValidationError("Memory directory not found")

        entries: dict[str, dict[str, Any]] = {}
        if spec.kind == "root":
            entries["README.md"] = {
                "name": "README.md",
                "path": MEMORY_README_PATH,
                "type": "file",
                "mime_type": "text/markdown",
                "size": len(MEMORY_README_CONTENT.encode("utf-8")),
            }

        prefix = spec.normalized_path.rstrip("/") + "/"

        for directory in sorted(existing_dirs):
            if directory in {spec.normalized_path, MEMORY_ROOT}:
                continue
            if not directory.startswith(prefix):
                continue
            relative = directory[len(prefix):]
            if "/" in relative or not relative:
                continue
            entries[relative] = {
                "name": relative,
                "path": directory,
                "type": "dir",
            }

        for document_path, document_id in documents:
            if not document_path.startswith(prefix):
                continue
            relative = document_path[len(prefix):]
            if "/" in relative or not relative:
                continue
            document = MemoryDocument.objects.get(id=document_id)
            content = str(document.content_markdown or "")
            entries[relative] = {
                "name": relative,
                "path": document_path,
                "type": "file",
                "mime_type": "text/markdown",
                "size": len(content.encode("utf-8")),
            }

        return [entries[key] for key in sorted(entries.keys())]

    return await sync_to_async(_impl, thread_sensitive=True)()


async def memory_path_exists(*, user, path: str) -> bool:
    try:
        spec = parse_memory_virtual_path(path)
    except ValidationError:
        return False

    if spec.kind in {"root", "readme"}:
        return True

    def _impl():
        directories, documents = _load_active_paths(user=user)
        document_paths = [path_value for path_value, _doc_id in documents]
        if spec.kind == "item":
            return spec.normalized_path in set(document_paths)
        existing_dirs = _collect_existing_directories(
            directories=directories,
            document_paths=document_paths,
        )
        return spec.normalized_path in existing_dirs

    return await sync_to_async(_impl, thread_sensitive=True)()


async def memory_is_dir(*, user, path: str) -> bool:
    try:
        spec = parse_memory_virtual_path(path)
    except ValidationError:
        return False
    if spec.kind == "readme":
        return False
    if spec.kind == "root":
        return True
    if spec.kind != "dir":
        return False
    return await memory_path_exists(user=user, path=spec.normalized_path)


async def mkdir_memory_dir(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "root":
        return MEMORY_ROOT
    if spec.kind != "dir":
        raise ValidationError("mkdir only supports memory directories")

    def _impl():
        if spec.parent_path and spec.parent_path != MEMORY_ROOT:
            directories, documents = _load_active_paths(user=user)
            existing_dirs = _collect_existing_directories(
                directories=directories,
                document_paths=[path_value for path_value, _doc_id in documents],
            )
            if spec.parent_path not in existing_dirs:
                raise ValidationError("Parent memory directory does not exist")
        if MemoryDocument.objects.filter(
            user=user,
            virtual_path=spec.normalized_path,
            status=MemoryRecordStatus.ACTIVE,
        ).exists():
            raise ValidationError("Cannot create memory directory over an existing file")
        directory = (
            MemoryDirectory.objects.filter(user=user, virtual_path=spec.normalized_path)
            .order_by("-updated_at", "-id")
            .first()
        )
        if directory is None:
            MemoryDirectory.objects.create(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            )
        elif directory.status != MemoryRecordStatus.ACTIVE:
            directory.status = MemoryRecordStatus.ACTIVE
            directory.save(update_fields=["status", "updated_at"])
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
        raise ValidationError("Memory writes must target Markdown files under /memory")

    markdown = _normalize_markdown(text)
    embeddings_enabled = await aget_embeddings_provider(user_id=user.id) is not None

    def _impl():
        if spec.parent_path and spec.parent_path != MEMORY_ROOT:
            directories, documents = _load_active_paths(user=user)
            existing_dirs = _collect_existing_directories(
                directories=directories,
                document_paths=[path_value for path_value, _doc_id in documents],
            )
            if spec.parent_path not in existing_dirs:
                raise ValidationError("Parent memory directory does not exist")

        title = _extract_title(markdown, fallback_path=spec.normalized_path)
        with transaction.atomic():
            document = (
                MemoryDocument.objects.select_for_update()
                .filter(user=user, virtual_path=spec.normalized_path)
                .order_by("-updated_at", "-id")
                .first()
            )
            if document is None:
                document = MemoryDocument.objects.create(
                    user=user,
                    virtual_path=spec.normalized_path,
                    title=title,
                    content_markdown=markdown,
                    source_thread=source_thread,
                    source_message=source_message,
                    status=MemoryRecordStatus.ACTIVE,
                )
            else:
                document.title = title
                document.content_markdown = markdown
                if document.status != MemoryRecordStatus.ACTIVE:
                    document.status = MemoryRecordStatus.ACTIVE
                if document.source_thread_id is None and source_thread is not None:
                    document.source_thread = source_thread
                if document.source_message_id is None and source_message is not None:
                    document.source_message = source_message
                document.save(
                    update_fields=[
                        "title",
                        "content_markdown",
                        "status",
                        "source_thread",
                        "source_message",
                        "updated_at",
                    ]
                )

            created_chunk_ids = _rebuild_document_chunks(document=document)

        warnings: tuple[str, ...] = ()
        if embeddings_enabled and created_chunk_ids:
            warnings = _schedule_chunk_embeddings(
                user_id=user.id,
                document_path=spec.normalized_path,
                chunk_ids=created_chunk_ids,
            )
        return MemoryVirtualEntry(
            path=spec.normalized_path,
            mime_type="text/markdown",
            size=len(markdown.encode("utf-8")),
            document_id=document.id,
            warnings=warnings,
        )

    return await sync_to_async(_impl, thread_sensitive=True)()


async def move_memory_path(*, user, source_path: str, destination_path: str) -> str:
    source_spec = parse_memory_virtual_path(source_path)
    destination_spec = parse_memory_virtual_path(destination_path)
    if source_spec.kind == "readme" or destination_spec.kind == "readme":
        raise ValidationError("README.md cannot be moved")
    if source_spec.kind != destination_spec.kind:
        raise ValidationError("Memory moves must preserve the source kind")

    def _impl():
        if destination_spec.parent_path and destination_spec.parent_path != MEMORY_ROOT:
            directories, documents = _load_active_paths(user=user)
            existing_dirs = _collect_existing_directories(
                directories=directories,
                document_paths=[path_value for path_value, _doc_id in documents],
            )
            if destination_spec.parent_path not in existing_dirs:
                raise ValidationError("Destination parent directory does not exist")

        if source_spec.kind == "item":
            document = (
                MemoryDocument.objects.filter(
                    user=user,
                    virtual_path=source_spec.normalized_path,
                    status=MemoryRecordStatus.ACTIVE,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if document is None:
                raise ValidationError("Memory document not found")
            if MemoryDocument.objects.filter(
                user=user,
                virtual_path=destination_spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            ).exclude(id=document.id).exists():
                raise ValidationError("A memory document already exists at the destination path")
            document.virtual_path = destination_spec.normalized_path
            document.save(update_fields=["virtual_path", "updated_at"])
            return destination_spec.normalized_path

        if source_spec.normalized_path == MEMORY_ROOT:
            raise ValidationError("The /memory root cannot be moved")
        if destination_spec.normalized_path.startswith(f"{source_spec.normalized_path}/"):
            raise ValidationError("Cannot move a directory inside itself")

        directories, documents = _load_active_paths(user=user)
        existing_dirs = _collect_existing_directories(
            directories=directories,
            document_paths=[path_value for path_value, _doc_id in documents],
        )
        if source_spec.normalized_path not in existing_dirs:
            raise ValidationError("Memory directory not found")

        source_prefix = source_spec.normalized_path.rstrip("/")
        destination_prefix = destination_spec.normalized_path.rstrip("/")
        doc_conflicts = []
        dir_conflicts = []
        for path_value, _doc_id in documents:
            if path_value == source_prefix or path_value.startswith(f"{source_prefix}/"):
                new_path = path_value.replace(source_prefix, destination_prefix, 1)
                doc_conflicts.append(new_path)
        for directory in directories:
            if directory == source_prefix or directory.startswith(f"{source_prefix}/"):
                new_path = directory.replace(source_prefix, destination_prefix, 1)
                dir_conflicts.append(new_path)
        if MemoryDocument.objects.filter(
            user=user,
            virtual_path__in=doc_conflicts,
            status=MemoryRecordStatus.ACTIVE,
        ).exclude(virtual_path__startswith=f"{source_prefix}/").exclude(virtual_path=source_prefix).exists():
            raise ValidationError("A memory document already exists under the destination directory")
        if MemoryDirectory.objects.filter(
            user=user,
            virtual_path__in=dir_conflicts,
            status=MemoryRecordStatus.ACTIVE,
        ).exclude(virtual_path__startswith=f"{source_prefix}/").exclude(virtual_path=source_prefix).exists():
            raise ValidationError("A memory directory already exists under the destination directory")

        with transaction.atomic():
            for document in MemoryDocument.objects.filter(
                user=user,
                status=MemoryRecordStatus.ACTIVE,
                virtual_path__startswith=f"{source_prefix}/",
            ):
                document.virtual_path = document.virtual_path.replace(source_prefix, destination_prefix, 1)
                document.save(update_fields=["virtual_path", "updated_at"])
            for document in MemoryDocument.objects.filter(
                user=user,
                status=MemoryRecordStatus.ACTIVE,
                virtual_path=source_prefix,
            ):
                document.virtual_path = destination_prefix
                document.save(update_fields=["virtual_path", "updated_at"])

            for directory in MemoryDirectory.objects.filter(
                user=user,
                status=MemoryRecordStatus.ACTIVE,
                virtual_path__startswith=f"{source_prefix}/",
            ):
                directory.virtual_path = directory.virtual_path.replace(source_prefix, destination_prefix, 1)
                directory.save(update_fields=["virtual_path", "updated_at"])
            for directory in MemoryDirectory.objects.filter(
                user=user,
                status=MemoryRecordStatus.ACTIVE,
                virtual_path=source_prefix,
            ):
                directory.virtual_path = destination_prefix
                directory.save(update_fields=["virtual_path", "updated_at"])

        return destination_spec.normalized_path

    return await sync_to_async(_impl, thread_sensitive=True)()


async def archive_memory_path(*, user, path: str) -> str:
    spec = parse_memory_virtual_path(path)
    if spec.kind == "readme":
        raise ValidationError("README.md cannot be removed")

    def _impl():
        if spec.kind == "item":
            document = (
                MemoryDocument.objects.filter(
                    user=user,
                    virtual_path=spec.normalized_path,
                    status=MemoryRecordStatus.ACTIVE,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if document is None:
                raise ValidationError("Memory document not found")
            with transaction.atomic():
                document.status = MemoryRecordStatus.ARCHIVED
                document.save(update_fields=["status", "updated_at"])
                MemoryChunk.objects.filter(
                    document=document,
                    status=MemoryRecordStatus.ACTIVE,
                ).update(status=MemoryRecordStatus.ARCHIVED, updated_at=timezone.now())
            return spec.normalized_path

        if spec.kind == "root":
            raise ValidationError("The /memory root cannot be removed")
        if _memory_dir_has_children(user=user, path=spec.normalized_path):
            raise ValidationError("Directory not empty")
        directory = (
            MemoryDirectory.objects.filter(
                user=user,
                virtual_path=spec.normalized_path,
                status=MemoryRecordStatus.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        if directory is None:
            raise ValidationError("Memory directory not found")
        directory.status = MemoryRecordStatus.ARCHIVED
        directory.save(update_fields=["status", "updated_at"])
        return spec.normalized_path

    return await sync_to_async(_impl, thread_sensitive=True)()


async def find_memory_paths(*, user, start_path: str, term: str = "") -> list[str]:
    spec = parse_memory_virtual_path(start_path)
    lowered_term = str(term or "").lower()

    def _matches(path_value: str) -> bool:
        if not lowered_term:
            return True
        return lowered_term in posixpath.basename(path_value).lower()

    def _impl():
        directories, documents = _load_active_paths(user=user)
        document_paths = [path_value for path_value, _doc_id in documents]
        existing_dirs = _collect_existing_directories(
            directories=directories,
            document_paths=document_paths,
        )

        if spec.kind == "readme":
            return [MEMORY_README_PATH] if _matches(MEMORY_README_PATH) else []

        if spec.kind == "item":
            return [spec.normalized_path] if spec.normalized_path in set(document_paths) and _matches(spec.normalized_path) else []

        matches: list[str] = []
        if spec.normalized_path == MEMORY_ROOT and _matches(MEMORY_README_PATH):
            matches.append(MEMORY_README_PATH)

        prefix = spec.normalized_path.rstrip("/") + "/"
        for directory in sorted(existing_dirs):
            if directory == MEMORY_ROOT and spec.normalized_path != MEMORY_ROOT:
                continue
            if directory == spec.normalized_path or directory.startswith(prefix):
                if _matches(directory):
                    matches.append(directory)
        for document_path in document_paths:
            if document_path.startswith(prefix) or document_path == spec.normalized_path:
                if _matches(document_path):
                    matches.append(document_path)
        return sorted(set(matches))

    return await sync_to_async(_impl, thread_sensitive=True)()


async def search_memory_items(
    *,
    query: str,
    user,
    limit: int = 10,
    under: str | None = None,
) -> dict[str, Any]:
    try:
        limit_value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("limit must be an integer") from exc
    limit_value = max(1, min(limit_value, MAX_MEMORY_SEARCH_LIMIT))

    query_text = str(query or "").strip()
    match_all = query_text in {"", "*"}
    query_vec = None
    if not match_all:
        query_vec = await resolve_query_vector(query=query_text, user_id=user.id)

    under_spec = None
    if under:
        under_spec = parse_memory_virtual_path(under)
        if under_spec.kind == "readme":
            raise ValidationError("README.md cannot be used as a memory search scope")

    def _apply_under_filter(queryset):
        if not under_spec:
            return queryset
        if under_spec.kind == "item":
            return queryset.filter(document__virtual_path=under_spec.normalized_path)
        prefix = under_spec.normalized_path.rstrip("/") + "/"
        if under_spec.normalized_path == MEMORY_ROOT:
            return queryset.filter(document__virtual_path__startswith=f"{MEMORY_ROOT}/")
        return queryset.filter(
            Q(document__virtual_path=under_spec.normalized_path)
            | Q(document__virtual_path__startswith=prefix)
        )

    def _impl(vec):
        qs = (
            MemoryChunk.objects.filter(
                document__user=user,
                document__status=MemoryRecordStatus.ACTIVE,
                status=MemoryRecordStatus.ACTIVE,
            )
            .select_related("document", "embedding")
            .order_by("-updated_at", "id")
        )
        qs = _apply_under_filter(qs)

        results: list[dict[str, Any]] = []
        engine = connection.vendor

        if match_all:
            for chunk in qs[:limit_value]:
                results.append(
                    {
                        "path": chunk.document.virtual_path,
                        "section_heading": chunk.heading,
                        "section_anchor": chunk.anchor,
                        "snippet": chunk.content_text[:240],
                        "score": None,
                        "signals": {"fts": False, "semantic": False},
                    }
                )
            return {
                "results": results,
                "notes": ["match-all mode: empty query or '*' returns recent memory chunks"],
            }

        if engine == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
            from pgvector.django import CosineDistance

            vector = SearchVector("content_text", config="english")
            search_query = SearchQuery(query_text)
            candidate_limit = 50

            fts_qs = (
                qs.annotate(fts_rank=SearchRank(vector, search_query))
                .filter(fts_rank__gt=0.0)
                .order_by(F("fts_rank").desc(), F("updated_at").desc())
            )
            fts_ids = list(fts_qs.values_list("id", flat=True)[:candidate_limit])

            semantic_ids: list[int] = []
            if vec is not None:
                semantic_qs = (
                    qs.filter(embedding__state=MemoryChunkEmbeddingState.READY)
                    .annotate(distance=CosineDistance("embedding__vector", vec))
                    .order_by(F("distance").asc(), F("updated_at").desc())
                )
                semantic_ids = list(semantic_qs.values_list("id", flat=True)[:candidate_limit])

            candidate_ids = list(dict.fromkeys([*semantic_ids, *fts_ids]))
            if not candidate_ids:
                return {"results": [], "notes": ["no matches"]}

            candidate_qs = (
                qs.filter(id__in=candidate_ids)
                .annotate(fts_rank=SearchRank(vector, search_query))
            )
            if vec is not None:
                candidate_qs = candidate_qs.annotate(distance=CosineDistance("embedding__vector", vec))
            else:
                candidate_qs = candidate_qs.annotate(distance=F("id") * 0.0)

            candidates = list(candidate_qs)

            def _semantic_sim(chunk) -> float | None:
                if vec is None:
                    return None
                distance = getattr(chunk, "distance", None)
                if distance is None:
                    return None
                return semantic_similarity_from_distance(distance, enabled=True)

            semantic_values = [value for value in (_semantic_sim(chunk) for chunk in candidates) if value is not None]
            fts_values = [float(getattr(chunk, "fts_rank", 0.0) or 0.0) for chunk in candidates]
            sem_min, sem_max = minmax_bounds(semantic_values)
            fts_min, fts_max = minmax_bounds(fts_values)

            scored: list[dict[str, Any]] = []
            for chunk in candidates:
                semantic_value = _semantic_sim(chunk)
                semantic_norm = minmax_normalize(semantic_value, vmin=sem_min, vmax=sem_max) if semantic_value is not None else 0.0
                fts_value = float(getattr(chunk, "fts_rank", 0.0) or 0.0)
                fts_norm = minmax_normalize(fts_value, vmin=fts_min, vmax=fts_max)
                final_score = blend_semantic_fts(semantic=semantic_norm, fts=fts_norm)
                scored.append(
                    {
                        "chunk": chunk,
                        "score": {
                            "final": float(final_score),
                            "fts_rank": fts_value,
                            "cosine_distance": float(getattr(chunk, "distance", 0.0) or 0.0)
                            if vec is not None and getattr(chunk, "distance", None) is not None
                            else None,
                        },
                    }
                )
            scored.sort(
                key=lambda row: (
                    -row["score"]["final"],
                    -(row["chunk"].updated_at.timestamp() if getattr(row["chunk"], "updated_at", None) else 0.0),
                    row["chunk"].id,
                )
            )
            for row in scored[:limit_value]:
                chunk = row["chunk"]
                results.append(
                    {
                        "path": chunk.document.virtual_path,
                        "section_heading": chunk.heading,
                        "section_anchor": chunk.anchor,
                        "snippet": chunk.content_text[:240],
                        "score": row["score"],
                        "signals": {"fts": True, "semantic": vec is not None},
                    }
                )
        else:
            filtered = qs.filter(content_text__icontains=query_text).order_by(F("updated_at").desc())
            for chunk in filtered[:limit_value]:
                results.append(
                    {
                        "path": chunk.document.virtual_path,
                        "section_heading": chunk.heading,
                        "section_anchor": chunk.anchor,
                        "snippet": chunk.content_text[:240],
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
