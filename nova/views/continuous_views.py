# nova/views/continuous_views.py

from __future__ import annotations

import datetime as dt
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from nova.continuous.utils import (
    append_continuous_user_message,
    enqueue_continuous_followups,
    ensure_continuous_thread,
    get_day_label_for_user,
)
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.models.UserObjects import UserParameters
from nova.message_panel import get_message_panel_agents, get_pending_interactions
from nova.tasks.conversation_tasks import summarize_day_segment_task
from nova.tasks.tasks import run_ai_task_celery
from nova.utils import markdown_to_html
from nova.message_attachments import get_message_attachment_template_context
from nova.message_rendering import prepare_messages_for_display, with_message_display_relations
from nova.message_submission import (
    MessageSubmissionError,
    SubmissionContext,
    submit_user_message,
)
from nova.message_utils import upload_message_attachments

_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _get_recent_messages_limit(user) -> int:
    params = UserParameters.objects.filter(user=user).only("continuous_default_messages_limit").first()
    if params:
        return params.continuous_default_messages_limit
    return UserParameters.CONTINUOUS_DEFAULT_MESSAGES_LIMIT_DEFAULT


def _parse_positive_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _apply_day_query(qs, raw_query: str | None):
    """Apply structured date filtering to day segments.

    Supported query formats:
    - YYYY
    - YYYY-MM
    - YYYY-MM-DD
    """
    applied_query = (raw_query or "").strip()
    if not applied_query:
        return qs, ""

    if _YEAR_RE.match(applied_query):
        return qs.filter(day_label__year=int(applied_query)), applied_query

    if _YEAR_MONTH_RE.match(applied_query):
        year_str, month_str = applied_query.split("-", 1)
        month = int(month_str)
        if 1 <= month <= 12:
            return qs.filter(day_label__year=int(year_str), day_label__month=month), applied_query
        return qs.none(), applied_query

    if _DATE_RE.match(applied_query):
        try:
            day_label = dt.date.fromisoformat(applied_query)
        except ValueError:
            return qs.none(), applied_query
        return qs.filter(day_label=day_label), applied_query

    return qs.none(), applied_query


def _group_day_segments_by_month(day_segments: list[DaySegment]):
    groups = []
    current_group = None
    current_key = None

    for segment in day_segments:
        key = segment.day_label.strftime("%Y-%m")
        if key != current_key:
            current_key = key
            current_group = {
                "month_key": key,
                "month_start": segment.day_label.replace(day=1),
                "items": [],
            }
            groups.append(current_group)
        current_group["items"].append(segment)

    return groups


@login_required(login_url="login")
def continuous_home(request):
    """Continuous mode landing page (server-rendered).

    V1: keep it simple; UI will be built in templates.
    """

    thread = ensure_continuous_thread(request.user)

    today_label = get_day_label_for_user(request.user)

    # Allow selecting a day via query param for initial render.
    # If invalid/missing: default to today.
    day_qs = request.GET.get("day")
    if day_qs:
        try:
            day_label = dt.date.fromisoformat(day_qs)
        except Exception:
            day_label = today_label
    else:
        day_label = today_label
    day_segment = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()

    # IMPORTANT:
    # Do NOT use the context key `messages` here.
    # Django's messages framework injects `messages` into templates; overriding it
    # causes each timeline message to render as a top-level UI notification.
    timeline_messages = []
    if day_segment and day_segment.starts_at_message_id:
        timeline_messages = prepare_messages_for_display(
            list(
                with_message_display_relations(
                    Message.objects.filter(
                        user=request.user,
                        thread=thread,
                        created_at__gte=day_segment.starts_at_message.created_at,
                    ).order_by("created_at", "id")
                )
            )
        )

    return render(
        request,
        "nova/continuous/index.html",
        {
            "continuous_thread_id": thread.id,
            "today_label": today_label,
            "day_label": day_label,
            "day_segment": day_segment,
            "timeline_messages": timeline_messages,
            "Actor": Actor,
        },
    )


@require_GET
@login_required(login_url="login")
def continuous_days(request):
    thread = ensure_continuous_thread(request.user)
    offset = max(0, _parse_positive_int(request.GET.get("offset"), 0))
    limit = _parse_positive_int(request.GET.get("limit"), 30)
    limit = max(1, min(limit, 100))
    query = request.GET.get("q")

    today = get_day_label_for_user(request.user)

    qs = DaySegment.objects.filter(user=request.user, thread=thread).order_by("-day_label")
    qs, applied_query = _apply_day_query(qs, query)
    total_count = qs.count()
    segments = list(qs[offset: offset + limit])
    next_offset = offset + len(segments)
    has_more = next_offset < total_count

    html = render_to_string(
        "nova/continuous/partials/day_selector.html",
        {
            "day_segments": segments,
            "day_groups": _group_day_segments_by_month(segments),
            "offset": offset,
            "limit": limit,
            "today": today,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "applied_query": applied_query,
            "default_recent_messages_limit": _get_recent_messages_limit(request.user),
        },
        request=request,
    )
    return JsonResponse(
        {
            "html": html,
            "count": len(segments),
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "applied_query": applied_query,
        }
    )


@require_GET
@login_required(login_url="login")
def continuous_day(request, day):
    thread = ensure_continuous_thread(request.user)
    try:
        day_label = dt.date.fromisoformat(day)
    except Exception:
        return JsonResponse({"error": "invalid_day"}, status=400)

    seg = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()
    return JsonResponse(
        {
            "day_label": day_label.isoformat(),
            "day_segment_id": seg.id if seg else None,
            "summary_markdown": seg.summary_markdown if seg else "",
            "summary_html": markdown_to_html(seg.summary_markdown) if seg and seg.summary_markdown else "",
            "updated_at": seg.updated_at.isoformat() if (seg and seg.updated_at) else None,
        }
    )


@require_GET
@login_required(login_url="login")
def continuous_messages(request):
    """Return the message container HTML for the continuous thread.

    This is a lightweight compatibility layer so we can reuse the same
    message rendering mechanics as Threads mode.

    V1: supports day scoping.
    """

    thread = ensure_continuous_thread(request.user)

    day_qs = request.GET.get("day")
    day_label = None
    if day_qs:
        try:
            day_label = dt.date.fromisoformat(day_qs)
        except Exception:
            return JsonResponse({"error": "invalid_day"}, status=400)

    recent_messages_limit = _get_recent_messages_limit(request.user)
    today_label = get_day_label_for_user(request.user)

    # Posting is only allowed for today's day label.
    # If the user is browsing a past day, the UI should be read-only.
    allow_posting = day_label is None or day_label == today_label

    user_agents, default_agent = get_message_panel_agents(
        request.user,
        thread_mode=Thread.Mode.CONTINUOUS,
        selected_agent_id=request.GET.get("agent_id"),
    )

    if day_label is None:
        latest_messages = list(
            with_message_display_relations(
                Message.objects.filter(user=request.user, thread=thread)
                .order_by("-created_at", "-id")[:recent_messages_limit]
            )
        )
        messages = prepare_messages_for_display(list(reversed(latest_messages)))
    else:
        seg = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()
        if not seg or not seg.starts_at_message_id:
            messages = []
        else:
            next_seg = (
                DaySegment.objects.filter(user=request.user, thread=thread, day_label__gt=day_label)
                .order_by("day_label")
                .first()
            )
            start_dt = seg.starts_at_message.created_at
            end_dt = next_seg.starts_at_message.created_at if (next_seg and next_seg.starts_at_message_id) else None
            qs = Message.objects.filter(user=request.user, thread=thread, created_at__gte=start_dt)
            if end_dt:
                qs = qs.filter(created_at__lt=end_dt)
            messages = prepare_messages_for_display(
                list(with_message_display_relations(qs.order_by("created_at", "id")))
            )

    # Keep template contract consistent with thread mode.
    return render(
        request,
        "nova/message_container.html",
        {
            "messages": messages,
            "thread_id": thread.id,
            "user_agents": user_agents,
            "default_agent": default_agent,
            "pending_interactions": get_pending_interactions(thread),
            "Actor": Actor,
            "allow_posting": allow_posting,
            "is_continuous_default_mode": day_label is None,
            "show_day_separators": day_label is None,
            "recent_messages_limit": recent_messages_limit,
            **get_message_attachment_template_context(),
        },
    )


@csrf_protect
@require_POST
@login_required(login_url="login")
def continuous_add_message(request):
    """Append a user message to the continuous thread and start agent execution."""

    def prepare_context(message_text: str) -> SubmissionContext:
        thread, message, segment, day_label, opened_new_day = append_continuous_user_message(
            request.user,
            message_text,
        )

        def before_message_delete(created_message):
            if segment and getattr(segment, "starts_at_message_id", None) == created_message.id:
                segment.delete()

        def after_dispatch():
            enqueue_continuous_followups(
                user=request.user,
                thread=thread,
                day_label=day_label,
                segment=segment,
                opened_new_day=opened_new_day,
                source="continuous_add_message",
            )

        return SubmissionContext(
            thread=thread,
            message=message,
            before_message_delete=before_message_delete,
            after_dispatch=after_dispatch,
            response_fields={
                "day_segment_id": segment.id,
                "day_label": day_label.isoformat(),
                "opened_new_day": opened_new_day,
            },
        )

    try:
        result = submit_user_message(
            user=request.user,
            message_text=request.POST.get("new_message", ""),
            selected_agent=request.POST.get("selected_agent"),
            response_mode=request.POST.get("response_mode"),
            thread_mode=Thread.Mode.CONTINUOUS,
            thread_files=[],
            message_attachments=request.FILES.getlist("message_attachments"),
            prepare_context=prepare_context,
            dispatcher_task=run_ai_task_celery,
            attachment_uploader=upload_message_attachments,
        )
    except MessageSubmissionError as exc:
        return JsonResponse(
            {"status": "ERROR", "message": exc.message},
            status=exc.status_code,
        )

    return JsonResponse(result.as_payload())


@csrf_protect
@require_POST
@login_required(login_url="login")
def continuous_regenerate_summary(request):
    """Manual summary refresh."""
    thread = ensure_continuous_thread(request.user)
    day = request.POST.get("day")
    if not day:
        day_label = get_day_label_for_user(request.user)
    else:
        try:
            day_label = dt.date.fromisoformat(day)
        except Exception:
            return JsonResponse({"error": "invalid_day"}, status=400)

    seg = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()
    if not seg:
        return JsonResponse({"error": "no_day_segment"}, status=404)

    # Manual summary refresh is allowed (even for today's segment).
    async_result = summarize_day_segment_task.delay(seg.id, mode="manual")
    return JsonResponse(
        {
            "status": "OK",
            "day_segment_id": seg.id,
            "day_label": day_label.isoformat(),
            "task_id": str(async_result.id),
        }
    )
