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

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F
from django.utils import timezone
from langchain_core.tools import StructuredTool

from nova.llm.hybrid_search import (
    blend_semantic_fts,
    minmax_bounds,
    minmax_normalize,
    resolve_query_vector,
    score_fts_saturated,
    semantic_similarity_from_distance,
)
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


def get_prompt_instructions() -> List[str]:
    """Tool-owned prompt guidance for continuous conversation recall."""
    return [
        "In continuous mode, use conversation_search first when you need to locate prior discussion evidence.",
        (
            "Then use conversation_get to ground exact passages, day summaries, "
            "or precise message ranges before answering."
        ),
        (
            "If recent-day summaries are truncated by context budget, immediately "
            "use conversation_search then conversation_get to recover missing details."
        ),
        "Do not use conversation tools for facts already present in the current turn context.",
    ]


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


_BASIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "de",
    "des",
    "du",
    "en",
    "et",
    "for",
    "from",
    "il",
    "in",
    "is",
    "la",
    "le",
    "les",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "un",
    "une",
    "with",
}


def _tokenize_for_local_match(text: str) -> List[str]:
    return [t for t in re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE) if t and t not in _BASIC_STOPWORDS]


def _trim_with_ellipses(text: str, start_cut: bool, end_cut: bool) -> str:
    s = (text or "").strip()
    if start_cut and s:
        s = f"… {s}"
    if end_cut and s:
        s = f"{s} …"
    return s


def _sentence_spans(text: str) -> List[tuple[int, int, str]]:
    src = text or ""
    spans: List[tuple[int, int, str]] = []
    for m in re.finditer(r"[^\n.!?]+(?:[.!?]+|$)", src, flags=re.UNICODE):
        start, end = m.span()
        sentence = src[start:end].strip()
        if sentence:
            spans.append((start, end, sentence))
    if not spans and src.strip():
        spans = [(0, len(src), src.strip())]
    return spans


def _local_lexical_anchor_window(text: str, query: str, max_len: int = 240) -> str:
    src = (text or "").strip()
    if not src:
        return ""
    if len(src) <= max_len:
        return src

    query_tokens = list(dict.fromkeys(_tokenize_for_local_match(query)))
    if not query_tokens:
        return _trim_with_ellipses(src[:max_len], start_cut=False, end_cut=True)

    best: Optional[tuple[float, int, int]] = None
    for start, end, sentence in _sentence_spans(src):
        sent_tokens = _tokenize_for_local_match(sentence)
        if not sent_tokens:
            continue
        token_set = set(sent_tokens)
        overlap = sum(1 for t in query_tokens if t in token_set)
        recall = overlap / max(1, len(query_tokens))

        sent_l = sentence.lower()
        phrase_bonus = 1.0 if (query or "").lower() in sent_l else 0.0

        first_hit_idx = next((i for i, tok in enumerate(sent_tokens) if tok in query_tokens), None)
        early_bonus = 0.0 if first_hit_idx is None else max(0.0, 1.0 - (first_hit_idx / max(1, len(sent_tokens))))

        length_penalty = max(0.0, (len(sentence) - 240) / 240)
        score = 0.6 * recall + 0.25 * phrase_bonus + 0.1 * early_bonus - 0.05 * length_penalty

        if best is None or score > best[0]:
            best = (score, start, end)

    if best is None:
        return _trim_with_ellipses(src[:max_len], start_cut=False, end_cut=True)

    _, start, end = best
    center = (start + end) // 2
    half = max_len // 2
    win_start = max(0, center - half)
    win_end = min(len(src), win_start + max_len)
    if win_end - win_start < max_len:
        win_start = max(0, win_end - max_len)
    snippet = src[win_start:win_end].strip()
    return _trim_with_ellipses(snippet, start_cut=(win_start > 0), end_cut=(win_end < len(src)))


def _focused_snippet(text: str, query: str, headline: Optional[str] = None, max_len: int = 240) -> str:
    hl = (headline or "").strip()
    if hl and "<mark>" in hl.lower():
        if len(hl) <= max_len:
            return hl
        return _trim_with_ellipses(hl[:max_len], start_cut=False, end_cut=True)
    return _local_lexical_anchor_window(text=text, query=query, max_len=max_len)


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
        has_range = from_message_id is not None and to_message_id is not None
        if message_id is None and day_segment_id is None and not has_range:
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
    """Search summaries + transcript chunks (hybrid FTS + semantic when enabled).

    Snippets are query-focused:
    - PostgreSQL FTS hits use ts_headline-style excerpts around matched terms
    - semantic-only hits use a local lexical anchor fallback
    """

    query = (query or "").strip()
    if not query:
        raise ValidationError("query must be a non-empty string")

    limit, offset = _validate_limit_offset(limit, offset)

    query_vec = await resolve_query_vector(user_id=agent.user.id, query=query)

    def _impl(vec: Optional[List[float]]):
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
            from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector
            from pgvector.django import CosineDistance

            q = SearchQuery(query)
            vector_summary = SearchVector("summary_markdown", config="english")
            vector_chunk = SearchVector("content_text", config="english")

            # 1) candidate retrieval
            K = 200

            seg_fts_ids = list(
                seg_qs.annotate(rank=SearchRank(vector_summary, q))
                .filter(rank__gt=0.0)
                .order_by(F("rank").desc(), F("day_label").desc())
                .values_list("id", flat=True)[:K]
            )
            chunk_fts_ids = list(
                chunk_qs.annotate(rank=SearchRank(vector_chunk, q))
                .filter(rank__gt=0.0)
                .order_by(F("rank").desc(), F("start_message_id").desc())
                .values_list("id", flat=True)[:K]
            )

            seg_sem_ids: List[int] = []
            chunk_sem_ids: List[int] = []
            if vec is not None:
                seg_sem_ids = list(
                    seg_qs.filter(embedding__state="ready")
                    .annotate(distance=CosineDistance("embedding__vector", vec))
                    .order_by(F("distance").asc(), F("updated_at").desc())
                    .values_list("id", flat=True)[:K]
                )
                chunk_sem_ids = list(
                    chunk_qs.filter(embedding__state="ready")
                    .annotate(distance=CosineDistance("embedding__vector", vec))
                    .order_by(F("distance").asc(), F("created_at").desc())
                    .values_list("id", flat=True)[:K]
                )

            seg_ids = list(dict.fromkeys([*seg_sem_ids, *seg_fts_ids]))
            chunk_ids = list(dict.fromkeys([*chunk_sem_ids, *chunk_fts_ids]))

            if not seg_ids and not chunk_ids:
                return {
                    "results": [],
                    "notes": ["no matches"],
                }

            # 2) candidate loading with both signals
            seg_candidates = seg_qs.filter(id__in=seg_ids).annotate(
                fts_rank=SearchRank(vector_summary, q),
                headline=SearchHeadline(
                    "summary_markdown",
                    q,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=35,
                    min_words=12,
                    short_word=2,
                    max_fragments=2,
                    fragment_delimiter=" … ",
                ),
            )
            chunk_candidates = (
                chunk_qs.filter(id__in=chunk_ids)
                .select_related("day_segment", "start_message")
                .annotate(
                    fts_rank=SearchRank(vector_chunk, q),
                    headline=SearchHeadline(
                        "content_text",
                        q,
                        start_sel="<mark>",
                        stop_sel="</mark>",
                        max_words=35,
                        min_words=12,
                        short_word=2,
                        max_fragments=2,
                        fragment_delimiter=" … ",
                    ),
                )
            )

            if vec is not None:
                seg_candidates = seg_candidates.annotate(distance=CosineDistance("embedding__vector", vec))
                chunk_candidates = chunk_candidates.annotate(distance=CosineDistance("embedding__vector", vec))
            else:
                seg_candidates = seg_candidates.annotate(distance=F("id") * 0.0)
                chunk_candidates = chunk_candidates.annotate(distance=F("id") * 0.0)

            seg_hits = list(seg_candidates)
            chunk_hits = list(chunk_candidates)

            scored: List[Dict[str, Any]] = []

            def _semantic_sim(distance: Optional[float]) -> float:
                return semantic_similarity_from_distance(distance, enabled=(vec is not None))

            semantic_vals = [
                _semantic_sim(getattr(seg, "distance", None)) for seg in seg_hits
            ] + [
                _semantic_sim(getattr(ch, "distance", None)) for ch in chunk_hits
            ]
            sem_min, sem_max = minmax_bounds(semantic_vals)

            for seg in seg_hits:
                fts_raw = float(getattr(seg, "fts_rank", 0.0) or 0.0)
                fts_sat = score_fts_saturated(fts_raw)
                sem_norm = minmax_normalize(
                    _semantic_sim(getattr(seg, "distance", None)),
                    vmin=sem_min,
                    vmax=sem_max,
                )
                blended = blend_semantic_fts(semantic=sem_norm, fts=fts_sat) if vec is not None else fts_sat
                score = 1.0 * _recency_multiplier(seg.updated_at) * blended
                scored.append(
                    {
                        "kind": "summary",
                        "score": score,
                        "day_label": seg.day_label.isoformat() if seg.day_label else None,
                        "day_segment_id": seg.id,
                        "summary_snippet": _focused_snippet(
                            text=seg.summary_markdown or "",
                            query=query,
                            headline=getattr(seg, "headline", None) if fts_raw > 0 else None,
                            max_len=240,
                        ),
                    }
                )

            for ch in chunk_hits:
                fts_raw = float(getattr(ch, "fts_rank", 0.0) or 0.0)
                fts_sat = score_fts_saturated(fts_raw)
                sem_norm = minmax_normalize(
                    _semantic_sim(getattr(ch, "distance", None)),
                    vmin=sem_min,
                    vmax=sem_max,
                )
                blended = blend_semantic_fts(semantic=sem_norm, fts=fts_sat) if vec is not None else fts_sat
                score = 0.92 * _recency_multiplier(ch.created_at) * blended
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
                        "snippet": _focused_snippet(
                            text=ch.content_text or "",
                            query=query,
                            headline=getattr(ch, "headline", None) if fts_raw > 0 else None,
                            max_len=240,
                        ),
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
            return {
                "results": page,
                "notes": [
                    "semantic ranking enabled only when embeddings provider is configured and vectors are ready",
                ],
            }

        # SQLite/tests fallback: icontains.
        seg_hits = seg_qs.filter(summary_markdown__icontains=query).order_by(F("day_label").desc())
        for seg in seg_hits[:200]:
            results.append(
                {
                    "kind": "summary",
                    "score": None,
                    "day_label": seg.day_label.isoformat() if seg.day_label else None,
                    "day_segment_id": seg.id,
                    "summary_snippet": _focused_snippet(
                        text=seg.summary_markdown or "",
                        query=query,
                        headline=None,
                        max_len=240,
                    ),
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
                    "snippet": _focused_snippet(
                        text=ch.content_text or "",
                        query=query,
                        headline=None,
                        max_len=240,
                    ),
                }
            )

        page = results[offset: offset + limit]
        return {"results": page}

    return await sync_to_async(_impl, thread_sensitive=True)(query_vec)


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
                    "query": {"type": "string", "description": "Search query (empty or * will select all)"},
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
