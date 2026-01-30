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

from nova.llm.llm_agent import LLMAgent
from nova.llm.embeddings import compute_embedding, get_embeddings_provider
from nova.models.Memory import MemoryItem, MemoryItemEmbedding, MemoryItemType, MemoryTheme

METADATA = {
    'name': 'Memory',
    'description': 'Access and manage structured long-term memory (search + add + get).',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


def _normalize_theme_slug(theme: str) -> str:
    theme = (theme or "").strip().lower()
    if not theme:
        raise ValidationError("theme must be a non-empty string")
    return theme.replace(" ", "-")


async def list_themes(agent: LLMAgent) -> Dict[str, Any]:
    """List themes available in the structured memory store."""

    def _impl():
        themes = MemoryTheme.objects.filter(user=agent.user).order_by("slug")
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

    provider = get_embeddings_provider()
    embeddings_enabled = provider is not None

    def _impl():
        item_type = type
        if item_type not in set(MemoryItemType.values):
            raise ValidationError(f"Invalid memory item type: {item_type}")

        if not content or not content.strip():
            raise ValidationError("content must be a non-empty string")

        theme_obj = None
        if theme:
            slug = _normalize_theme_slug(theme)
            theme_obj, _ = MemoryTheme.objects.get_or_create(
                user=agent.user,
                slug=slug,
                defaults={"display_name": theme.strip()},
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
                "has_vector": bool(getattr(embedding, "vector", None)),
            },
        }

    return await sync_to_async(_impl, thread_sensitive=True)()


async def search(
    query: str,
    agent: LLMAgent,
    limit: int = 10,
    theme: Optional[str] = None,
    types: Optional[List[str]] = None,
    recency_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Search memory items.

    Current implementation:
    - Uses PostgreSQL FTS when available.
    - Falls back to icontains for non-Postgres DBs (e.g. SQLite tests).
    - pgvector semantic ranking will be enabled later once query embeddings exist.
    """

    if not query or not query.strip():
        raise ValidationError("query must be a non-empty string")

    try:
        limit = int(limit)
    except Exception as e:
        raise ValidationError("limit must be an integer") from e
    limit = max(1, min(limit, 50))

    provider = get_embeddings_provider()
    query_vec = await compute_embedding(query.strip()) if provider else None

    def _impl(vec: Optional[List[float]]):
        qs = MemoryItem.objects.select_related("theme").filter(user=agent.user)

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

        if engine == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
            from pgvector.django import CosineDistance

            vector = SearchVector("content", config="english")
            q = SearchQuery(query)

            qs_ranked = qs.annotate(rank=SearchRank(vector, q)).filter(rank__gt=0.0)

            if vec is not None:
                # Hybrid ranking:
                # - semantic (cosine distance) for items with ready vectors
                # - still keep FTS rank for exact-token boost
                qs_ranked = qs_ranked.select_related("embedding").annotate(
                    distance=CosineDistance("embedding__vector", vec)
                ).filter(
                    embedding__vector__isnull=False,
                    embedding__state="ready",
                ).order_by(
                    F("distance").asc(nulls_last=True),
                    F("rank").desc(),
                    F("created_at").desc(),
                )
            else:
                qs_ranked = qs_ranked.order_by(F("rank").desc(), F("created_at").desc())

            for item in qs_ranked[:limit]:
                results.append(
                    {
                        "id": item.id,
                        "theme": item.theme.slug if item.theme else None,
                        "type": item.type,
                        "content_snippet": item.content[:240],
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                        "score": {
                            "fts_rank": float(getattr(item, "rank", 0.0) or 0.0),
                            "cosine_distance": float(getattr(item, "distance", 0.0)) if vec is not None else None,
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
            coroutine=lambda query, limit=10, theme=None, types=None, recency_days=None: search(
                query=query,
                limit=limit,
                theme=theme,
                types=types,
                recency_days=recency_days,
                agent=agent,
            ),
            name="search",
            description="Search long-term memory items relevant to a query",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
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
            name="add",
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
            name="get",
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
            coroutine=lambda: list_themes(agent),
            name="list_themes",
            description="List themes in long-term memory",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
    ]
