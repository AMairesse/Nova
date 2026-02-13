"""Celery tasks for continuous discussion mode."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import List, Optional, Tuple

from asgiref.sync import sync_to_async
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction

from nova.llm.llm_agent import LLMAgent
from nova.models.ConversationEmbedding import DaySegmentEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.UserObjects import UserProfile
from nova.tasks.conversation_embedding_tasks import compute_day_segment_embedding_task
from nova.utils import strip_thinking_blocks

logger = logging.getLogger(__name__)

User = get_user_model()


async def _publish_task_update(task_id: str | None, message_type: str, data: dict | None = None) -> None:
    """Publish task progress events to the task websocket group."""
    if not task_id:
        return
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    payload = {"type": message_type, **(data or {})}
    try:
        await channel_layer.group_send(
            f"task_{task_id}",
            {"type": "task_update", "message": payload},
        )
    except Exception:
        logger.exception("[summarize_day_segment] websocket publish failed task_id=%s", task_id)


def _format_messages_for_summary(messages: List[Message]) -> str:
    """Normalize message list into a compact text transcript.

    V1 rules:
    - keep only USER/AGENT messages
    - ignore tool payloads (stored in Message.internal_data)
    - cap each message to avoid runaway prompts
    """

    lines: List[str] = []
    for m in messages:
        if m.actor not in (Actor.USER, Actor.AGENT):
            continue
        role = "User" if m.actor == Actor.USER else "Agent"
        text = (m.text or "").strip()
        if not text:
            continue
        if len(text) > 1500:
            text = text[:1500] + "\n…(truncated)…"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _build_day_summary_prompt(day_label: str, transcript: str) -> str:
    return _build_day_summary_prompt_with_context(day_label, transcript)


def _build_day_summary_prompt_with_context(
    day_label: str,
    transcript: str,
    *,
    previous_summaries: List[tuple[str, str]] | None = None,
    current_summary: str = "",
    delta_mode: bool = False,
) -> str:
    previous_summaries = previous_summaries or []
    previous_block_lines: List[str] = []
    for prev_day, prev_summary in previous_summaries:
        summary_text = (prev_summary or "").strip()
        if not summary_text:
            continue
        previous_block_lines.append(f"### {prev_day}\n{summary_text}")
    previous_block = "\n\n".join(previous_block_lines) or "(none)"

    current_summary = (current_summary or "").strip()
    if not current_summary:
        current_summary = "(none)"

    transcript_label = (
        "New messages since the previous summary for this day"
        if delta_mode
        else "Messages for this day"
    )

    return (
        "You are generating a day summary for a continuous discussion.\n"
        "You can use available tools if useful.\n"
        "If long-term memory tools are available, store durable facts, decisions, and follow-ups.\n"
        "Write a concise Markdown summary for this day.\n"
        "Keep Goals/Open loops/Next steps only if they are still valid; remove resolved or stale items.\n"
        "If a section has no item, write '- None'.\n\n"
        "Output template:\n"
        "## Summary\n"
        "<short narrative>\n\n"
        "## Goals\n"
        "- ...\n\n"
        "## Decisions\n"
        "- ...\n\n"
        "## Open loops\n"
        "- ...\n\n"
        "## Next steps\n"
        "- ...\n\n"
        "## Memory updates\n"
        "- ...\n\n"
        f"Day to summarize: {day_label}\n\n"
        "Previous day summaries (most recent first):\n"
        f"{previous_block}\n\n"
        f"Current summary for {day_label} (if any):\n"
        f"{current_summary}\n\n"
        f"{transcript_label}:\n"
        f"{transcript}\n\n"
        "Markdown Summary:\n"
    )


async def _summarize_day_segment_async(day_segment_id: int, mode: str, task_id: str | None = None) -> dict:
    # Django ORM is synchronous and must not run in an async context.
    # Use sync_to_async for all DB access.
    force_full_refresh = (mode or "").strip().lower() == "manual"

    async def _fetch_segment_and_messages() -> Tuple[
        Optional[DaySegment], List[Message], str, List[tuple[str, str]], bool
    ]:
        def _impl() -> Tuple[Optional[DaySegment], List[Message], str, List[tuple[str, str]], bool]:
            segment = (
                DaySegment.objects.select_related("thread", "user", "starts_at_message")
                .filter(id=day_segment_id)
                .first()
            )
            if not segment:
                return None, [], "", [], False

            # Bound the day segment using the next segment start.
            next_seg = (
                DaySegment.objects.filter(user=segment.user, thread=segment.thread, day_label__gt=segment.day_label)
                .order_by("day_label")
                .select_related("starts_at_message")
                .first()
            )
            start_dt = segment.starts_at_message.created_at
            end_dt = next_seg.starts_at_message.created_at if (next_seg and next_seg.starts_at_message_id) else None

            qs = Message.objects.filter(
                user=segment.user,
                thread=segment.thread,
                created_at__gte=start_dt,
            )
            if end_dt:
                qs = qs.filter(created_at__lt=end_dt)

            current_summary = (segment.summary_markdown or "").strip()
            delta_mode = (not force_full_refresh) and bool(current_summary and segment.summary_until_message_id)
            if delta_mode:
                qs = qs.filter(id__gt=segment.summary_until_message_id)

            msgs = list(qs.order_by("created_at", "id"))

            prev_segments = list(
                DaySegment.objects.filter(
                    user=segment.user,
                    thread=segment.thread,
                    day_label__lt=segment.day_label,
                    summary_markdown__isnull=False,
                )
                .exclude(summary_markdown="")
                .order_by("-day_label")
            )
            previous_summaries = [
                (seg.day_label.isoformat(), (seg.summary_markdown or "").strip())
                for seg in prev_segments[:2]
                if (seg.summary_markdown or "").strip()
            ]
            return segment, msgs, current_summary, previous_summaries, delta_mode

        return await sync_to_async(_impl, thread_sensitive=True)()

    async def _fetch_default_agent_config(user):
        def _impl():
            try:
                return UserProfile.objects.select_related("default_agent").get(user=user).default_agent
            except UserProfile.DoesNotExist:
                return None

        return await sync_to_async(_impl, thread_sensitive=True)()

    await _publish_task_update(task_id, "progress_update", {"progress_log": "Preparing day summary..."})
    segment, messages, current_summary, previous_summaries, delta_mode = await _fetch_segment_and_messages()
    if not segment:
        await _publish_task_update(task_id, "task_error", {"message": "Day segment not found", "category": "summary"})
        return {"status": "not_found", "day_segment_id": day_segment_id}

    user = segment.user
    agent_config = await _fetch_default_agent_config(user)
    if not agent_config:
        await _publish_task_update(
            task_id,
            "task_error",
            {"message": "No default agent configured for summary generation", "category": "summary"},
        )
        return {"status": "error", "error": "no_default_agent", "day_segment_id": day_segment_id}

    transcript = _format_messages_for_summary(messages)
    if not transcript.strip():
        await _publish_task_update(
            task_id,
            "continuous_summary_ready",
            {
                "day_segment_id": day_segment_id,
                "day_label": segment.day_label.isoformat(),
                "updated_at": segment.updated_at.isoformat() if segment.updated_at else None,
            },
        )
        await _publish_task_update(
            task_id,
            "task_complete",
            {
                "result": "summary_up_to_date",
                "thread_id": segment.thread_id,
                "thread_subject": segment.thread.subject,
            },
        )
        return {"status": "ok", "day_segment_id": day_segment_id, "summary": ""}

    prompt = _build_day_summary_prompt_with_context(
        str(segment.day_label),
        transcript,
        previous_summaries=previous_summaries,
        current_summary=current_summary,
        delta_mode=delta_mode,
    )

    agent = await LLMAgent.create(user=user, thread=segment.thread, agent_config=agent_config)
    ephemeral_thread_id = f"day_summary_{segment.id}_{uuid.uuid4()}"
    try:
        await _publish_task_update(task_id, "progress_update", {"progress_log": "Generating day summary..."})
        resp = await agent.ainvoke(
            prompt,
            silent_mode=True,
            thread_id_override=ephemeral_thread_id,
        )
        if isinstance(resp, dict) and "__interrupt__" in resp:
            raise RuntimeError("Summary agent requested user input, which is unsupported for nightly summarization.")
        summary_md = strip_thinking_blocks(resp)

        # Persist summary (sync ORM in sync_to_async).
        def _persist():
            with transaction.atomic():
                seg = DaySegment.objects.select_for_update().get(id=segment.id)
                seg.summary_markdown = summary_md
                # Summary boundary: last message included in the transcript.
                # V1: we summarize from `starts_at_message` up to the last message we read.
                seg.summary_until_message_id = messages[-1].id if messages else None
                seg.save(update_fields=["summary_markdown", "summary_until_message", "updated_at"])

                emb, _ = DaySegmentEmbedding.objects.get_or_create(
                    user=seg.user,
                    day_segment=seg,
                )
                emb.state = "pending"
                emb.error = None
                emb.vector = None
                emb.save(update_fields=["state", "error", "vector", "updated_at"])
                try:
                    compute_day_segment_embedding_task.delay(emb.id)
                except Exception:
                        logger.exception(
                            "[summarize_day_segment] failed to enqueue summary embedding day_segment_id=%s",
                            seg.id,
                        )
                return seg.day_label.isoformat(), seg.updated_at.isoformat() if seg.updated_at else None, seg.thread_id

        day_label_iso, updated_at_iso, thread_id = await sync_to_async(_persist, thread_sensitive=True)()
        await _publish_task_update(task_id, "progress_update", {"progress_log": "Day summary updated."})
        await _publish_task_update(
            task_id,
            "continuous_summary_ready",
            {
                "day_segment_id": day_segment_id,
                "day_label": day_label_iso,
                "updated_at": updated_at_iso,
            },
        )
        await _publish_task_update(
            task_id,
            "task_complete",
            {
                "result": "summary_complete",
                "thread_id": thread_id,
                "thread_subject": segment.thread.subject,
            },
        )

        logger.info(
            "[summarize_day_segment] ok day_segment_id=%s mode=%s chars=%s",
            day_segment_id,
            mode,
            len(summary_md),
        )
        return {
            "status": "ok",
            "day_segment_id": day_segment_id,
            "day_label": day_label_iso,
            "updated_at": updated_at_iso,
            "mode": mode,
        }
    finally:
        checkpointer = getattr(agent, "checkpointer", None)
        if checkpointer is not None:
            try:
                await checkpointer.adelete_thread(ephemeral_thread_id)
            except Exception:
                logger.warning(
                    "[summarize_day_segment] failed cleanup for ephemeral checkpoint_id=%s",
                    ephemeral_thread_id,
                )
        await agent.cleanup()


@shared_task(bind=True, name="summarize_day_segment")
def summarize_day_segment_task(self, day_segment_id: int, mode: str = "heuristic"):
    """Generate/update DaySegment.summary_markdown.

    V1 implementation:
    - uses the user's default agent LLM to generate a Markdown summary
    - persists to DaySegment only (no synthetic system Message)
    """

    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    try:
        return asyncio.run(_summarize_day_segment_async(day_segment_id=day_segment_id, mode=mode, task_id=task_id))
    except Exception as e:
        logger.exception("[summarize_day_segment] failed day_segment_id=%s", day_segment_id)
        asyncio.run(
            _publish_task_update(
                task_id,
                "progress_update",
                {"progress_log": "Summary generation failed, retrying..."},
            )
        )
        try:
            raise self.retry(countdown=60, exc=e)
        except MaxRetriesExceededError:
            asyncio.run(
                _publish_task_update(
                    task_id,
                    "task_error",
                    {"message": str(e), "category": "summary"},
                )
            )
            raise


def _daysegment_needs_nightly_refresh(seg: DaySegment) -> bool:
    """Return True if the segment should be summarized (or re-summarized).

    Policy:
    - only for day segments strictly older than today (handled by caller)
    - summarize if no summary exists
    - or if new messages were appended after the last summarized message
    """

    if not (seg.summary_markdown or "").strip():
        return True

    # If we have a summary but no boundary pointer, be conservative and refresh.
    if not seg.summary_until_message_id:
        return True

    # Check for messages after the boundary within this day segment.
    # NOTE: Day segments are defined by their starts_at_message and the next segment's start.
    next_seg = (
        DaySegment.objects.filter(user=seg.user, thread=seg.thread, day_label__gt=seg.day_label)
        .order_by("day_label")
        .only("id", "starts_at_message_id")
        .first()
    )
    end_dt = None
    if next_seg and next_seg.starts_at_message_id:
        end_dt = next_seg.starts_at_message.created_at

    qs = Message.objects.filter(
        user=seg.user,
        thread=seg.thread,
        created_at__gte=seg.starts_at_message.created_at,
        id__gt=seg.summary_until_message_id,
    )
    if end_dt:
        qs = qs.filter(created_at__lt=end_dt)

    return qs.exists()


@shared_task(bind=True, name="continuous_nightly_daysegment_summaries")
def nightly_summarize_continuous_daysegments_task(self):
    """Nightly maintenance task (celery-beat).

    Runs at 02:00 UTC daily.

    For all DaySegments with day_label < today (UTC):
    - generate a summary if missing
    - or regenerate if new messages exist after `summary_until_message`
    """

    from django.utils import timezone

    today = timezone.now().date()

    # Prefetch starts_at_message to avoid N+1 on created_at.
    segs = (
        DaySegment.objects.select_related("starts_at_message", "thread", "user")
        .filter(day_label__lt=today)
        .order_by("day_label", "id")
    )

    queued = 0
    for seg in segs:
        try:
            if _daysegment_needs_nightly_refresh(seg):
                summarize_day_segment_task.delay(seg.id, mode="nightly")
                queued += 1
        except Exception:
            logger.exception("[nightly_summarize] failed scheduling day_segment_id=%s", seg.id)

    logger.info("[nightly_summarize] queued=%s", queued)
    return {"status": "ok", "queued": queued}


@shared_task(bind=True, name="continuous_nightly_daysegment_summaries_for_user")
def nightly_summarize_continuous_daysegments_for_user_task(self, user_id: int):
    """Nightly maintenance task, scoped to a single user.

    This is the task scheduled via user-owned TaskDefinition so users can edit the time.
    """

    from django.utils import timezone

    today = timezone.now().date()
    segs = (
        DaySegment.objects.select_related("starts_at_message", "thread", "user")
        .filter(user_id=user_id, day_label__lt=today)
        .order_by("day_label", "id")
    )

    # IMPORTANT:
    # We must process in chronological order and *execute* each summarization before the next,
    # so that carry-over (yesterday summary) is up-to-date.
    processed = 0
    updated = 0
    for seg in segs:
        processed += 1
        try:
            if _daysegment_needs_nightly_refresh(seg):
                asyncio.run(_summarize_day_segment_async(day_segment_id=seg.id, mode="nightly"))
                updated += 1
        except Exception:
            logger.exception("[nightly_summarize_for_user] failed day_segment_id=%s", seg.id)

    logger.info(
        "[nightly_summarize_for_user] user_id=%s processed=%s updated=%s",
        user_id,
        processed,
        updated,
    )
    return {"status": "ok", "user_id": user_id, "processed": processed, "updated": updated}
