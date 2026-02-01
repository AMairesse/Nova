# nova/tools/builtins/conversation.py

"""Nova builtin tool: Conversation recall (continuous discussion mode).

This exposes two tools:
- conversation.search: search day summaries + transcript chunks (FTS-only in V1)
- conversation.get: fetch summary by day_segment_id OR messages window around a message_id

Important:
- Multi-tenant: always scoped to agent.user
- V1: offset-based pagination for search
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F
from django.utils import timezone
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.models.DaySegment import DaySegment
from nova.models.Message import Message
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk

METADATA = {
    "name": "Conversation",
    "description": "Conversation-level recall for continuous discussion mode (search + get).",
    "requires_config": False,
    "config_fields": [],
    "test_function": None,
    "test_function_args": [],
}


def _validate_limit_offset(limit: int, offset: int) -> tuple[int, int]:
    try:
        limit = int(limit)
    except Exception as e:
        raise ValidationError("limit must be an integer") from e
    try:
        offset = int(offset)
    except Exception as e:
        raise ValidationError("offset must be an integer") from e

    limit = max(1, min(limit, 50))
    offset = max(0, min(offset, 500))
    return limit, offset


def _recency_multiplier(created_at) -> float:
    if not created_at:
        return 0.8
    age = timezone.now() - created_at
    if age <= timedelta(hours=24):
        return 1.0
    if age <= timedelta(days=7):
        return 0.9
    return 0.8


async def conversation_get(
    agent: LLMAgent,
    message_id: Optional[int] = None,
    day_segment_id: Optional[int] = None,
    from_message_id: Optional[int] = None,
    to_message_id: Optional[int] = None,
    limit: int = 30,
    before_message_id: Optional[int] = None,
    after_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch a summary or a window of messages.

    Mirrors the simplified spec.
    """

    def _impl():
        # Summary fetch
        if day_segment_id is not None and message_id is None:
            seg = (
                DaySegment.objects.filter(user=agent.user, id=day_segment_id)
                .select_related("thread")
                .first()
            )
            if not seg:
                return {"error": "not_found"}
            return {
                "day_segment_id": seg.id,
                "day_label": seg.day_label.isoformat() if seg.day_label else None,
                "summary_markdown": seg.summary_markdown or "",
                "updated_at": seg.updated_at.isoformat() if seg.updated_at else None,
            }

        # Messages fetch
        if message_id is None and day_segment_id is None:
            return {"error": "invalid_request"}

        try:
            limit_i = int(limit)
        except Exception as e:
            raise ValidationError("limit must be an integer") from e
        limit_i = max(1, min(limit_i, 30))

        qs = Message.objects.filter(user=agent.user)

        # Scope to continuous thread if available
        if getattr(agent, "thread", None):
            qs = qs.filter(thread=agent.thread)

        if day_segment_id is not None:
            seg = DaySegment.objects.filter(user=agent.user, id=day_segment_id).first()
            if not seg:
                return {"error": "not_found"}
            qs = qs.filter(thread=seg.thread, created_at__gte=seg.starts_at_message.created_at)

        if from_message_id is not None and to_message_id is not None:
            qs = qs.filter(id__gte=from_message_id, id__lte=to_message_id).order_by("id")
            msgs = list(qs[:limit_i])
            return {
                "messages": [
                    {
                        "message_id": m.id,
                        "role": m.actor,
                        "content": m.text,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in msgs
                ],
                "truncated": len(msgs) >= limit_i,
            }

        if message_id is not None:
            anchor = qs.filter(id=message_id).first()
            if not anchor:
                return {"error": "not_found"}

            if before_message_id is not None:
                msgs = list(qs.filter(id__lt=before_message_id).order_by("-id")[:limit_i])
                msgs.reverse()
            elif after_message_id is not None:
                msgs = list(qs.filter(id__gt=after_message_id).order_by("id")[:limit_i])
            else:
                half = limit_i // 2
                before = list(qs.filter(id__lt=message_id).order_by("-id")[:half])
                before.reverse()
                after = list(qs.filter(id__gt=message_id).order_by("id")[: (limit_i - len(before) - 1)])
                msgs = before + [anchor] + after

            return {
                "messages": [
                    {
                        "message_id": m.id,
                        "role": m.actor,
                        "content": m.text,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in msgs
                ],
                "truncated": len(msgs) >= limit_i,
            }

        return {"error": "invalid_request"}

    return await sync_to_async(_impl, thread_sensitive=True)()


async def conversation_search(
    query: str,
    agent: LLMAgent,
    day: Optional[str] = None,
    recency_days: int = 14,
    limit: int = 6,
    offset: int = 0,
) -> Dict[str, Any]:
    """Search summaries + transcript chunks (FTS-only)."""

    query = (query or "").strip()
    if not query:
        raise ValidationError("query must be a non-empty string")

    limit, offset = _validate_limit_offset(limit, offset)

    def _impl():
        # Resolve scope: either within a day, or last N days.
        seg_qs = DaySegment.objects.filter(user=agent.user)
        chunk_qs = TranscriptChunk.objects.filter(user=agent.user)

        if getattr(agent, "thread", None) and getattr(agent.thread, "mode", None) == Thread.Mode.CONTINUOUS:
            seg_qs = seg_qs.filter(thread=agent.thread)
            chunk_qs = chunk_qs.filter(thread=agent.thread)

        if day:
            try:
                day_label = timezone.datetime.fromisoformat(day).date()
            except Exception as e:
                raise ValidationError("day must be YYYY-MM-DD") from e
            seg = seg_qs.filter(day_label=day_label).first()
            if not seg:
                return {"results": [], "notes": ["no matches"]}
            seg_qs = seg_qs.filter(id=seg.id)
            chunk_qs = chunk_qs.filter(day_segment=seg)
        else:
            cutoff = timezone.now().date() - timedelta(days=int(recency_days))
            seg_qs = seg_qs.filter(day_label__gte=cutoff)
            # Chunk cutoff by day_segment.day_label for the same recency window.
            chunk_qs = chunk_qs.filter(day_segment__day_label__gte=cutoff)

        engine = connection.vendor
        results: List[Dict[str, Any]] = []

        if engine == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

            q = SearchQuery(query)

            # Summaries
            seg_hits = (
                seg_qs.annotate(
                    rank=SearchRank(SearchVector("summary_markdown", config="english"), q)
                )
                .filter(rank__gt=0.0)
                .order_by(F("rank").desc(), F("day_label").desc())
            )

            # Transcript chunks
            chunk_hits = (
                chunk_qs.annotate(
                    rank=SearchRank(SearchVector("content_text", config="english"), q)
                )
                .filter(rank__gt=0.0)
                .select_related("day_segment", "start_message")
                .order_by(F("rank").desc(), F("start_message_id").desc())
            )

            scored: List[Dict[str, Any]] = []
            for seg in seg_hits[:200]:
                fts_raw = float(seg.rank or 0.0)
                fts = fts_raw / (fts_raw + 1.0)
                score = fts * _recency_multiplier(seg.updated_at)
                scored.append(
                    {
                        "kind": "summary",
                        "score": score,
                        "day_label": seg.day_label.isoformat() if seg.day_label else None,
                        "day_segment_id": seg.id,
                        "summary_snippet": (seg.summary_markdown or "")[:240],
                    }
                )

            for ch in chunk_hits[:400]:
                fts_raw = float(ch.rank or 0.0)
                fts = fts_raw / (fts_raw + 1.0)
                score = fts * _recency_multiplier(ch.created_at)
                day_label = None
                if ch.day_segment and ch.day_segment.day_label:
                    day_label = ch.day_segment.day_label.isoformat()
                scored.append(
                    {
                        "kind": "message",
                        "score": score,
                        "day_label": day_label,
                        "day_segment_id": ch.day_segment_id,
                        "message_id": ch.start_message_id,
                        "snippet": (ch.content_text or "")[:240],
                    }
                )

            scored.sort(
                key=lambda r: (
                    -float(r.get("score", 0.0) or 0.0),
                    r.get("day_label") or "",
                    r.get("day_segment_id") or 0,
                )
            )
            page = scored[offset: offset + limit]
            return {"results": page}

        # SQLite/tests fallback: icontains.
        seg_hits = seg_qs.filter(summary_markdown__icontains=query).order_by(F("day_label").desc())
        for seg in seg_hits[:200]:
            results.append(
                {
                    "kind": "summary",
                    "score": None,
                    "day_label": seg.day_label.isoformat() if seg.day_label else None,
                    "day_segment_id": seg.id,
                    "summary_snippet": (seg.summary_markdown or "")[:240],
                }
            )

        chunk_hits = chunk_qs.filter(content_text__icontains=query).select_related("day_segment").order_by(
            F("start_message_id").desc()
        )
        for ch in chunk_hits[:400]:
            day_label = None
            if ch.day_segment and ch.day_segment.day_label:
                day_label = ch.day_segment.day_label.isoformat()
            results.append(
                {
                    "kind": "message",
                    "score": None,
                    "day_label": day_label,
                    "day_segment_id": ch.day_segment_id,
                    "message_id": ch.start_message_id,
                    "snippet": (ch.content_text or "")[:240],
                }
            )

        page = results[offset: offset + limit]
        return {"results": page}

    return await sync_to_async(_impl, thread_sensitive=True)()


async def get_functions(tool, agent: LLMAgent):
    return [
        StructuredTool.from_function(
            coroutine=lambda query, day=None, recency_days=14, limit=6, offset=0: conversation_search(
                query=query,
                day=day,
                recency_days=recency_days,
                limit=limit,
                offset=offset,
                agent=agent,
            ),
            name="conversation_search",
            description="Search day summaries and transcript chunks from the continuous discussion.",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "day": {"type": "string", "description": "Optional YYYY-MM-DD"},
                    "recency_days": {"type": "integer", "default": 14},
                    "limit": {"type": "integer", "default": 6},
                    "offset": {"type": "integer", "default": 0},
                },
                "required": ["query"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda message_id=None,
            day_segment_id=None,
            from_message_id=None,
            to_message_id=None,
            limit=30,
            before_message_id=None,
            after_message_id=None: conversation_get(
                agent=agent,
                message_id=message_id,
                day_segment_id=day_segment_id,
                from_message_id=from_message_id,
                to_message_id=to_message_id,
                limit=limit,
                before_message_id=before_message_id,
                after_message_id=after_message_id,
            ),
            name="conversation_get",
            description="Fetch a day summary or a window of messages for grounding.",
            args_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "integer"},
                    "day_segment_id": {"type": "integer"},
                    "from_message_id": {"type": "integer"},
                    "to_message_id": {"type": "integer"},
                    "limit": {"type": "integer", "default": 30},
                    "before_message_id": {"type": "integer"},
                    "after_message_id": {"type": "integer"},
                },
                "required": [],
            },
        ),
    ]
