# nova/continuous/context_builder.py

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from django.utils import timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from nova.continuous.utils import get_day_label_for_user
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message


@dataclass(frozen=True)
class ContinuousContextSnapshot:
    today: dt.date
    previous_summary_1_day: Optional[dt.date]
    previous_summary_2_day: Optional[dt.date]
    previous_summary_1_updated_at: Optional[dt.datetime]
    previous_summary_2_updated_at: Optional[dt.datetime]
    previous_summary_1_hash: str
    previous_summary_2_hash: str
    previous_summaries_token_budget: int
    previous_summaries_truncated: bool
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
            (
                "previous_summary_1_day="
                f"{snapshot.previous_summary_1_day.isoformat() if snapshot.previous_summary_1_day else ''}"
            ),
            (
                "previous_summary_2_day="
                f"{snapshot.previous_summary_2_day.isoformat() if snapshot.previous_summary_2_day else ''}"
            ),
            f"previous_summary_1_updated_at={_fmt_dt(snapshot.previous_summary_1_updated_at)}",
            f"previous_summary_2_updated_at={_fmt_dt(snapshot.previous_summary_2_updated_at)}",
            f"previous_summary_1_hash={snapshot.previous_summary_1_hash}",
            f"previous_summary_2_hash={snapshot.previous_summary_2_hash}",
            f"previous_summaries_token_budget={snapshot.previous_summaries_token_budget}",
            f"previous_summaries_truncated={int(snapshot.previous_summaries_truncated)}",
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


def _approx_tokens(text: str) -> int:
    s = (text or "").strip()
    if not s:
        return 0
    # Conservative/cheap estimate.
    return max(1, len(s) // 4)


def _trim_to_token_budget(text: str, budget_tokens: int) -> tuple[str, bool]:
    s = (text or "").strip()
    if not s:
        return "", False
    if budget_tokens <= 0:
        return "", bool(s)

    # Word-level trim for deterministic output.
    words = re.findall(r"\S+", s, flags=re.UNICODE)
    out_words: list[str] = []
    used = 0
    for w in words:
        wt = _approx_tokens(w)
        if used + wt > budget_tokens:
            break
        out_words.append(w)
        used += wt

    trimmed = " ".join(out_words).strip()
    truncated = len(trimmed) < len(s)
    if truncated and trimmed:
        trimmed += "\n\n…(summary truncated due to strict context budget)…"
    return trimmed, truncated


def _make_summary_system_message(label: str, summary_md: str) -> List[BaseMessage]:
    """Inject summary as a SystemMessage (continuous policy)."""

    summary_md = (summary_md or "").strip()
    if not summary_md:
        return []

    msg = SystemMessage(
        content=f"[{label}]\n{summary_md}",
        additional_kwargs={"summary": True, "label": label, "source": "day_segment"},
    )
    return [msg]


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
    - Previous two *available* summarized days (before today) as System messages.
    - Strict combined token budget for these summaries.
    - If truncated by budget, include explicit fallback guidance toward
      conversation_search / conversation_get.
    - Today raw window: messages between today's DaySegment start and next DaySegment start.

    Returns (snapshot, messages)
    """

    today = get_day_label_for_user(user)
    previous_summary_segments = list(
        DaySegment.objects.filter(
            user=user,
            thread=thread,
            day_label__lt=today,
            summary_markdown__isnull=False,
        )
        .exclude(summary_markdown="")
        .select_related("starts_at_message", "summary_until_message")
        .order_by("-day_label")[:2]
    )
    p1_seg = previous_summary_segments[0] if len(previous_summary_segments) >= 1 else None
    p2_seg = previous_summary_segments[1] if len(previous_summary_segments) >= 2 else None
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

    # Build summary messages for previous days with strict budget.
    PREVIOUS_SUMMARIES_TOKEN_BUDGET = 4000

    p1_summary_raw = (p1_seg.summary_markdown if p1_seg else "") or ""
    p2_summary_raw = (p2_seg.summary_markdown if p2_seg else "") or ""
    p1_label = f"Summary of {p1_seg.day_label.isoformat()}" if p1_seg else ""
    p2_label = f"Summary of {p2_seg.day_label.isoformat()}" if p2_seg else ""

    # Prioritize J-1 then J-2 under one strict budget.
    budget_left = PREVIOUS_SUMMARIES_TOKEN_BUDGET
    p1_budget = min(budget_left, _approx_tokens(p1_summary_raw))
    p1_summary, p1_truncated = _trim_to_token_budget(p1_summary_raw, p1_budget)
    budget_left = max(0, budget_left - _approx_tokens(p1_summary))

    p2_budget = min(budget_left, _approx_tokens(p2_summary_raw))
    p2_summary, p2_truncated = _trim_to_token_budget(p2_summary_raw, p2_budget)

    previous_summaries_truncated = p1_truncated or p2_truncated

    out: List[BaseMessage] = []
    if p1_summary:
        out.extend(_make_summary_system_message(p1_label, p1_summary))
    if p2_summary:
        out.extend(_make_summary_system_message(p2_label, p2_summary))
    if previous_summaries_truncated:
        out.append(
            SystemMessage(
                content=(
                    "[Continuous context notice]\n"
                    "Some previous-day summaries were truncated due to strict token budget. "
                    "If more historical detail is needed, use conversation_search first, "
                    "then conversation_get to ground exact passages."
                ),
                additional_kwargs={"summary_notice": True, "truncated": True},
            )
        )

    today_last_message_id: Optional[int] = None

    # If we have a summary for today and it defines a boundary, inject the
    # summary and include only messages AFTER the summary boundary.
    #
    # This avoids losing same-day information covered by the summary.
    today_summary_until_message_id: Optional[int] = None
    if t_seg:
        today_summary_raw = (t_seg.summary_markdown or "").strip()
        if today_summary_raw and t_seg.summary_until_message_id:
            today_summary_until_message_id = t_seg.summary_until_message_id
            out.extend(
                _make_summary_system_message(
                    f"Summary of {today.isoformat()}",
                    today_summary_raw,
                )
            )

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
        previous_summary_1_day=p1_seg.day_label if p1_seg else None,
        previous_summary_2_day=p2_seg.day_label if p2_seg else None,
        previous_summary_1_updated_at=p1_seg.updated_at if p1_seg else None,
        previous_summary_2_updated_at=p2_seg.updated_at if p2_seg else None,
        previous_summary_1_hash=hashlib.sha256(p1_summary.encode("utf-8")).hexdigest() if p1_summary else "",
        previous_summary_2_hash=hashlib.sha256(p2_summary.encode("utf-8")).hexdigest() if p2_summary else "",
        previous_summaries_token_budget=PREVIOUS_SUMMARIES_TOKEN_BUDGET,
        previous_summaries_truncated=previous_summaries_truncated,
        today_updated_at=t_seg.updated_at if t_seg else None,
        today_start_dt=today_start_dt,
        today_end_dt=today_end_dt,
        today_summary_until_message_id=today_summary_until_message_id,
        today_last_message_id=today_last_message_id,
    )
    return snapshot, out
