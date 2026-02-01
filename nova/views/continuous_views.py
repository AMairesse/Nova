# nova/views/continuous_views.py

from __future__ import annotations

import datetime as dt
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user, get_or_create_day_segment
from nova.models.AgentConfig import AgentConfig
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.Task import Task, TaskStatus
from nova.tasks.conversation_tasks import summarize_day_segment_task
from nova.tasks.tasks import run_ai_task_celery
from nova.tasks.transcript_index_tasks import index_transcript_append_task
from nova.utils import markdown_to_html

logger = logging.getLogger(__name__)


@login_required(login_url="login")
def continuous_home(request):
    """Continuous mode landing page (server-rendered).

    V1: keep it simple; UI will be built in templates.
    """

    thread = ensure_continuous_thread(request.user)
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
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 30))
    limit = max(1, min(limit, 100))

    qs = DaySegment.objects.filter(user=request.user, thread=thread).order_by("-day_label")
    segments = list(qs[offset: offset + limit])

    html = render_to_string(
        "nova/continuous/partials/day_selector.html",
        {"day_segments": segments, "offset": offset, "limit": limit},
        request=request,
    )
    return JsonResponse({"html": html, "count": len(segments)})


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
        }
    )


@csrf_protect
@require_POST
@login_required(login_url="login")
def continuous_add_message(request):
    """Append a user message to the continuous thread and start agent execution."""

    new_message = request.POST.get("new_message", "")
    selected_agent = request.POST.get("selected_agent")

    thread = ensure_continuous_thread(request.user)
    day_label = get_day_label_for_user(request.user)

    # Create user message first (defines the day segment start if first message of the day)
    msg = thread.add_message(new_message, actor=Actor.USER)

    # Ensure day segment exists for today.
    # If we just opened a new day segment, we trigger summarization for the
    # previous day segment (V1 policy: summarize only when a new day starts).
    seg = DaySegment.objects.filter(user=request.user, thread=thread, day_label=day_label).first()
    opened_new_day = False
    if not seg:
        seg = get_or_create_day_segment(request.user, thread, day_label, starts_at_message=msg)
        opened_new_day = True

    # Resolve agent (no dropdown in V1 continuous UI, but keep compatibility for now)
    agent_config = None
    if selected_agent:
        agent_config = get_object_or_404(AgentConfig, id=selected_agent, user=request.user)
    else:
        agent_config = getattr(getattr(request.user, "userprofile", None), "default_agent", None)

    task = Task.objects.create(user=request.user, thread=thread, agent_config=agent_config, status=TaskStatus.PENDING)

    run_ai_task_celery.delay(task.id, request.user.id, thread.id, agent_config.id if agent_config else None, msg.id)

    # Kick lightweight continuous maintenance tasks (best-effort)
    try:
        index_transcript_append_task.delay(seg.id)
    except Exception:
        pass

    # Summary policy (V1): do NOT summarize on every post.
    # Only summarize the previous day once we open a new day segment.
    if opened_new_day:
        try:
            prev_seg = (
                DaySegment.objects.filter(user=request.user, thread=thread, day_label__lt=day_label)
                .order_by("-day_label")
                .first()
            )
            if prev_seg:
                summarize_day_segment_task.delay(prev_seg.id, mode="nightly")
        except Exception:
            pass

    return JsonResponse({"status": "OK", "thread_id": thread.id, "task_id": task.id, "day_segment_id": seg.id})


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
    summarize_day_segment_task.delay(seg.id, mode="manual")
    return JsonResponse({"status": "OK", "day_segment_id": seg.id})
