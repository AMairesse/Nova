# nova/continuous/context_builder.py

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from django.utils import timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from nova.continuous.utils import get_day_label_for_user
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message


@dataclass(frozen=True)
class ContinuousContextSnapshot:
    today: dt.date
    yesterday: dt.date
    yesterday_updated_at: Optional[dt.datetime]
    today_updated_at: Optional[dt.datetime]
    today_start_dt: Optional[dt.datetime]
    today_end_dt: Optional[dt.datetime]
    today_summary_until_message_id: Optional[int]
    today_last_message_id: Optional[int]


def compute_continuous_context_fingerprint(snapshot: ContinuousContextSnapshot) -> str:
    """Stable fingerprint for deciding whether the checkpoint must be rebuilt.

    V1: keep it deterministic and cheap to compute.
    """

    def _fmt_dt(v: Optional[dt.datetime]) -> str:
        if not v:
            return ""
        # Ensure stable format
        if timezone.is_naive(v):
            v = timezone.make_aware(v, timezone.get_current_timezone())
        return v.astimezone(dt.timezone.utc).isoformat()

    raw = "|".join(
        [
            f"today={snapshot.today.isoformat()}",
            f"yesterday={snapshot.yesterday.isoformat()}",
            f"yesterday_updated_at={_fmt_dt(snapshot.yesterday_updated_at)}",
            f"today_updated_at={_fmt_dt(snapshot.today_updated_at)}",
            f"today_start_dt={_fmt_dt(snapshot.today_start_dt)}",
            f"today_end_dt={_fmt_dt(snapshot.today_end_dt)}",
            f"today_summary_until_message_id={snapshot.today_summary_until_message_id or ''}",
            f"today_last_message_id={snapshot.today_last_message_id or ''}",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)…"


def _make_summary_pair(label: str, summary_md: str) -> List[BaseMessage]:
    """Strict alternation hack: Human(summary) + AI(ack)."""

    summary_md = (summary_md or "").strip()
    if not summary_md:
        return []

    human = HumanMessage(
        content=f"[{label}]\n{summary_md}",
        additional_kwargs={"summary": True, "label": label},
    )
    ack = AIMessage(
        content="Understood.",
        additional_kwargs={"summary_ack": True, "label": label},
    )
    return [human, ack]


def _message_to_langchain(m: Message) -> Optional[BaseMessage]:
    """Convert a DB Message to a LangChain message.

    V1 trimming:
    - ignore SYSTEM
    - hard cap content length
    - ignore internal_data tool payloads (they are not part of Message.text)
    """

    if m.actor == Actor.SYSTEM:
        return None

    content = _truncate(m.text or "", limit=2500)
    if not content:
        return None

    if m.actor == Actor.USER:
        return HumanMessage(content=content)
    if m.actor == Actor.AGENT:
        return AIMessage(content=content)
    return None


def load_continuous_context(
    user,
    thread,
    *,
    exclude_message_id: Optional[int] = None,
) -> Tuple[ContinuousContextSnapshot, List[BaseMessage]]:
    """Build the messages to inject for the continuous checkpoint.

    Policy:
    - Yesterday summary (if present)
    - Today summary (if present)
    - Today raw window: messages between today's DaySegment start and next DaySegment start

    Returns (snapshot, messages)
    """

    today = get_day_label_for_user(user)
    yesterday = today - dt.timedelta(days=1)

    y_seg = (
        DaySegment.objects.filter(user=user, thread=thread, day_label=yesterday)
        .select_related("starts_at_message", "summary_until_message")
        .first()
    )
    t_seg = (
        DaySegment.objects.filter(user=user, thread=thread, day_label=today)
        .select_related("starts_at_message", "summary_until_message")
        .first()
    )

    # Determine today window bounds from day segments.
    today_start_dt = None
    today_end_dt = None
    if t_seg and t_seg.starts_at_message_id:
        today_start_dt = t_seg.starts_at_message.created_at
        next_seg = (
            DaySegment.objects.filter(user=user, thread=thread, day_label__gt=today)
            .order_by("day_label")
            .first()
        )
        if next_seg and next_seg.starts_at_message_id:
            today_end_dt = next_seg.starts_at_message.created_at

    # Build messages
    out: List[BaseMessage] = []
    if y_seg and y_seg.summary_markdown:
        out.extend(_make_summary_pair("Yesterday summary", y_seg.summary_markdown))
    if t_seg and t_seg.summary_markdown:
        out.extend(_make_summary_pair("Today summary", t_seg.summary_markdown))

    today_last_message_id: Optional[int] = None

    # If we have a summary for today, and it defines a boundary, we only include
    # messages AFTER the summary boundary.
    today_summary_until_message_id: Optional[int] = None
    if t_seg and t_seg.summary_until_message_id:
        today_summary_until_message_id = t_seg.summary_until_message_id

    if today_start_dt:
        qs = Message.objects.filter(user=user, thread=thread, created_at__gte=today_start_dt)
        if today_end_dt:
            qs = qs.filter(created_at__lt=today_end_dt)
        if today_summary_until_message_id:
            qs = qs.filter(id__gt=today_summary_until_message_id)
        if exclude_message_id:
            qs = qs.exclude(id=exclude_message_id)
        for m in qs.order_by("created_at", "id"):
            msg = _message_to_langchain(m)
            if msg is not None:
                out.append(msg)
            today_last_message_id = m.id

    snapshot = ContinuousContextSnapshot(
        today=today,
        yesterday=yesterday,
        yesterday_updated_at=y_seg.updated_at if y_seg else None,
        today_updated_at=t_seg.updated_at if t_seg else None,
        today_start_dt=today_start_dt,
        today_end_dt=today_end_dt,
        today_summary_until_message_id=today_summary_until_message_id,
        today_last_message_id=today_last_message_id,
    )
    return snapshot, out
