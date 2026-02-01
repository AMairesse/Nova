# nova/tasks/transcript_index_tasks.py

"""Transcript indexing tasks for continuous discussion mode (V1).

V1 goal: append-only chunk creation for the continuous thread so `conversation.search`
can query `TranscriptChunk` instead of raw Message rows.
"""

from __future__ import annotations

import logging
from typing import List

from celery import shared_task
from django.db import transaction

from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.TranscriptChunk import TranscriptChunk

logger = logging.getLogger(__name__)


def _normalize_message_text(m: Message) -> str:
    # Ignore system messages in chunks.
    if m.actor not in (Actor.USER, Actor.AGENT):
        return ""
    text = (m.text or "").strip()
    if not text:
        return ""
    # Hard trim per-message to avoid huge chunks.
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)…"
    prefix = "User" if m.actor == Actor.USER else "Agent"
    return f"{prefix}: {text}"


def _estimate_tokens(text: str) -> int:
    # Consistent with nova.utils.estimate_tokens; keep local to avoid import cycles.
    return len(text) // 4 + 1


def _index_transcript_append(day_segment_id: int) -> dict:
    """Append-only transcript chunk creation.

    IMPORTANT: this task runs in a normal Celery worker process.
    Django ORM is synchronous, so keep this function synchronous to avoid
    `SynchronousOnlyOperation`.
    """

    seg = (
        DaySegment.objects.select_related("thread", "user", "starts_at_message")
        .filter(id=day_segment_id)
        .first()
    )
    if not seg:
        return {"status": "not_found", "day_segment_id": day_segment_id}

    # Find last indexed end message within this day segment.
    last_chunk = (
        TranscriptChunk.objects.filter(user=seg.user, thread=seg.thread, day_segment=seg)
        .order_by("-end_message__created_at", "-end_message_id")
        .select_related("end_message")
        .first()
    )
    from_dt = seg.starts_at_message.created_at
    if last_chunk:
        from_dt = last_chunk.end_message.created_at

    # Collect new messages for this day (best effort):
    # - created_at >= from_dt
    # - same thread/user
    msgs = list(
        Message.objects.filter(
            user=seg.user,
            thread=seg.thread,
            created_at__gte=from_dt,
        ).order_by("created_at", "id")
    )
    if not msgs:
        return {"status": "ok", "day_segment_id": day_segment_id, "created": 0}

    # Build chunks ~600 tokens with ~100 token overlap.
    target_tokens = 600
    overlap_tokens = 100

    created = 0
    i = 0
    while i < len(msgs):
        start_idx = i
        buf_lines: List[str] = []
        tok = 0
        start_msg = None
        end_msg = None

        while i < len(msgs) and tok < target_tokens:
            m = msgs[i]
            line = _normalize_message_text(m)
            i += 1
            if not line:
                continue
            if start_msg is None:
                start_msg = m
            end_msg = m
            buf_lines.append(line)
            tok += _estimate_tokens(line)

        if start_msg is None or end_msg is None:
            break

        content_text = "\n".join(buf_lines)
        content_hash = TranscriptChunk.compute_hash(content_text, start_msg.id, end_msg.id)

        with transaction.atomic():
            obj, was_created = TranscriptChunk.objects.get_or_create(
                user=seg.user,
                thread=seg.thread,
                day_segment=seg,
                start_message=start_msg,
                end_message=end_msg,
                defaults={
                    "content_text": content_text,
                    "content_hash": content_hash,
                    "token_estimate": tok,
                },
            )
            if not was_created:
                # Best-effort update if content hash differs.
                if obj.content_hash != content_hash:
                    obj.content_text = content_text
                    obj.content_hash = content_hash
                    obj.token_estimate = tok
                    obj.save(update_fields=["content_text", "content_hash", "token_estimate", "updated_at"])
            else:
                created += 1

        # Apply overlap: rewind index by some messages until we have overlap_tokens.
        if overlap_tokens > 0 and i < len(msgs):
            back_tok = 0
            j = i - 1
            while j > start_idx and back_tok < overlap_tokens:
                line = _normalize_message_text(msgs[j])
                if line:
                    back_tok += _estimate_tokens(line)
                j -= 1
            i = max(j + 1, start_idx + 1)

    return {"status": "ok", "day_segment_id": day_segment_id, "created": created}


@shared_task(bind=True, name="index_transcript_append")
def index_transcript_append_task(self, day_segment_id: int):
    """Append-only transcript chunk creation."""
    try:
        return _index_transcript_append(day_segment_id=day_segment_id)
    except Exception as e:
        logger.exception("[index_transcript_append] failed day_segment_id=%s", day_segment_id)
        raise self.retry(countdown=60, exc=e)
