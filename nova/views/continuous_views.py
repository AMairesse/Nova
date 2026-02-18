# nova/views/continuous_views.py

from __future__ import annotations

import datetime as dt
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from nova.continuous.utils import (
    append_continuous_user_message,
    enqueue_continuous_followups,
    ensure_continuous_thread,
    get_day_label_for_user,
)
from nova.models.AgentConfig import AgentConfig
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Actor, Message
from nova.models.Task import Task, TaskStatus
from nova.models.UserObjects import UserParameters
from nova.tasks.conversation_tasks import summarize_day_segment_task
from nova.tasks.tasks import run_ai_task_celery
from nova.utils import markdown_to_html

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

    # Allow selecting a day via query param for initial render.
    # If invalid/missing: default to today.
    day_qs = request.GET.get("day")
    if day_qs:
        try:
            day_label = dt.date.fromisoformat(day_qs)
        except Exception:
            day_label = get_day_label_for_user(request.user)
    else:
        day_label = get_day_label_for_user(request.user)
    day_segment = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()

    # IMPORTANT:
    # Do NOT use the context key `messages` here.
    # Django's messages framework injects `messages` into templates; overriding it
    # causes each timeline message to render as a top-level UI notification.
    timeline_messages = []
    if day_segment and day_segment.starts_at_message_id:
        timeline_messages = list(
            Message.objects.filter(
                user=request.user,
                thread=thread,
                created_at__gte=day_segment.starts_at_message.created_at,
            ).order_by("created_at", "id")
        )
        for m in timeline_messages:
            m.rendered_html = markdown_to_html(m.text)

    return render(
        request,
        "nova/continuous/index.html",
        {
            "continuous_thread_id": thread.id,
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

    user_agents = AgentConfig.objects.filter(user=request.user, is_tool=False)
    agent_id = request.GET.get("agent_id")
    default_agent = None
    if agent_id:
        default_agent = AgentConfig.objects.filter(id=agent_id, user=request.user).first()
    if not default_agent:
        default_agent = getattr(getattr(request.user, "userprofile", None), "default_agent", None)

    if day_label is None:
        latest_messages = list(
            Message.objects.filter(user=request.user, thread=thread)
            .order_by("-created_at", "-id")[:recent_messages_limit]
        )
        messages = list(reversed(latest_messages))
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
            messages = list(qs.order_by("created_at", "id"))
    for m in messages:
        m.rendered_html = markdown_to_html(m.text)

    pending_interactions = (
        Interaction.objects.filter(
            thread=thread,
            status=InteractionStatus.PENDING,
        )
        .select_related("task", "agent_config")
        .order_by("created_at", "id")
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
            "pending_interactions": pending_interactions,
            "Actor": Actor,
            "allow_posting": allow_posting,
            "is_continuous_default_mode": day_label is None,
            "show_day_separators": day_label is None,
            "recent_messages_limit": recent_messages_limit,
        },
    )


@csrf_protect
@require_POST
@login_required(login_url="login")
def continuous_add_message(request):
    """Append a user message to the continuous thread and start agent execution."""

    new_message = request.POST.get("new_message", "")
    selected_agent = request.POST.get("selected_agent")

    thread, msg, seg, day_label, opened_new_day = append_continuous_user_message(request.user, new_message)

    # Resolve agent (no dropdown in V1 continuous UI, but keep compatibility for now)
    agent_config = None
    if selected_agent:
        agent_config = get_object_or_404(AgentConfig, id=selected_agent, user=request.user)
    else:
        agent_config = getattr(getattr(request.user, "userprofile", None), "default_agent", None)

    task = Task.objects.create(user=request.user, thread=thread, agent_config=agent_config, status=TaskStatus.PENDING)

    run_ai_task_celery.delay(task.id, request.user.id, thread.id, agent_config.id if agent_config else None, msg.id)

    enqueue_continuous_followups(
        user=request.user,
        thread=thread,
        day_label=day_label,
        segment=seg,
        opened_new_day=opened_new_day,
        source="continuous_add_message",
    )

    # Match the JSON contract expected by [`MessageManager.handleFormSubmit()`](nova/static/js/message-manager.js:235)
    # so Continuous can reuse the same client logic as Threads.
    message_data = {
        "id": msg.id,
        "text": new_message,
        "actor": msg.actor,
        "file_count": 0,
        "internal_data": msg.internal_data or {},
    }

    return JsonResponse(
        {
            "status": "OK",
            "message": message_data,
            "thread_id": thread.id,
            "task_id": task.id,
            "day_segment_id": seg.id,
            "day_label": day_label.isoformat(),
            "opened_new_day": opened_new_day,
            # Keep response shape compatible with thread mode callers.
            "threadHtml": None,
            "uploaded_file_ids": [],
        }
    )


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
