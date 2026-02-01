"""Celery tasks for continuous discussion mode."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple

from asgiref.sync import sync_to_async
from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from langchain_core.messages import HumanMessage

from nova.llm.llm_agent import LLMAgent
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.UserObjects import UserProfile

logger = logging.getLogger(__name__)

User = get_user_model()


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
    return (
        "You are generating a day summary for a continuous discussion.\n"
        "Write a concise Markdown summary using this template and nothing else.\n\n"
        "Template:\n"
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
        f"Day: {day_label}\n\n"
        "Transcript:\n"
        f"{transcript}\n\n"
        "Markdown Summary:\n"
    )


async def _summarize_day_segment_async(day_segment_id: int, mode: str) -> dict:
    # Django ORM is synchronous and must not run in an async context.
    # Use sync_to_async for all DB access.

    async def _fetch_segment_and_messages() -> Tuple[Optional[DaySegment], List[Message]]:
        def _impl() -> Tuple[Optional[DaySegment], List[Message]]:
            segment = (
                DaySegment.objects.select_related("thread", "user", "starts_at_message")
                .filter(id=day_segment_id)
                .first()
            )
            if not segment:
                return None, []
            msgs = list(
                Message.objects.filter(
                    user=segment.user,
                    thread=segment.thread,
                    created_at__gte=segment.starts_at_message.created_at,
                ).order_by("created_at")
            )
            return segment, msgs

        return await sync_to_async(_impl, thread_sensitive=True)()

    async def _fetch_default_agent_config(user):
        def _impl():
            try:
                return UserProfile.objects.select_related("default_agent").get(user=user).default_agent
            except UserProfile.DoesNotExist:
                return None

        return await sync_to_async(_impl, thread_sensitive=True)()

    segment, messages = await _fetch_segment_and_messages()
    if not segment:
        return {"status": "not_found", "day_segment_id": day_segment_id}

    user = segment.user
    agent_config = await _fetch_default_agent_config(user)
    if not agent_config:
        return {"status": "error", "error": "no_default_agent", "day_segment_id": day_segment_id}

    transcript = _format_messages_for_summary(messages)
    if not transcript.strip():
        return {"status": "ok", "day_segment_id": day_segment_id, "summary": ""}

    prompt = _build_day_summary_prompt(str(segment.day_label), transcript)

    agent = await LLMAgent.create(user=user, thread=segment.thread, agent_config=agent_config)
    try:
        # Use the same underlying chat model as the agent; call it directly.
        llm = agent.create_llm_agent()
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        summary_md = (getattr(resp, "content", None) or str(resp)).strip()

        # Persist summary (sync ORM in sync_to_async).
        def _persist():
            with transaction.atomic():
                seg = DaySegment.objects.select_for_update().get(id=segment.id)
                seg.summary_markdown = summary_md
                # Summary boundary: last message included in the transcript.
                # V1: we summarize from `starts_at_message` up to the last message we read.
                seg.summary_until_message_id = messages[-1].id if messages else None
                seg.save(update_fields=["summary_markdown", "summary_until_message", "updated_at"])

        await sync_to_async(_persist, thread_sensitive=True)()

        logger.info(
            "[summarize_day_segment] ok day_segment_id=%s mode=%s chars=%s",
            day_segment_id,
            mode,
            len(summary_md),
        )
        return {"status": "ok", "day_segment_id": day_segment_id, "mode": mode}
    finally:
        await agent.cleanup()


@shared_task(bind=True, name="summarize_day_segment")
def summarize_day_segment_task(self, day_segment_id: int, mode: str = "heuristic"):
    """Generate/update DaySegment.summary_markdown.

    V1 implementation:
    - uses the user's default agent LLM to generate a Markdown summary
    - persists to DaySegment only (no synthetic system Message)
    """

    try:
        return asyncio.run(_summarize_day_segment_async(day_segment_id=day_segment_id, mode=mode))
    except Exception as e:
        logger.exception("[summarize_day_segment] failed day_segment_id=%s", day_segment_id)
        raise self.retry(countdown=60, exc=e)
