from django.http import JsonResponse, Http404
from django.db.models import DateTimeField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.message_panel import (
    get_message_panel_agents,
    get_pending_interactions,
    get_user_default_agent,
)
from nova.tasks.tasks import run_ai_task_celery, summarize_thread_task
from nova.thread_titles import build_default_thread_subject
import logging

from nova.file_utils import batch_upload_files
from nova.message_attachments import get_message_attachment_template_context
from nova.message_composer import get_message_composer_template_context
from nova.message_rendering import prepare_messages_for_display, with_message_display_relations
from nova.message_submission import (
    MessageSubmissionError,
    SubmissionContext,
    submit_user_message,
)
from nova.runtime.compaction import get_compactable_message_count, get_compaction_error
from nova.message_utils import upload_message_attachments
from nova.realtime.sidebar_updates import publish_file_update
from nova.threads import ThreadDeletionError, delete_thread_for_user

logger = logging.getLogger(__name__)

MAX_THREADS_DISPLAYED = 10
MESSAGE_PAGE_SIZE = 50


def _classic_threads_for_user(user, *, include_archived=False):
    """Return classic threads in stable last-activity order."""
    latest_message = Message.objects.filter(
        thread=OuterRef("pk"), user=user
    ).order_by("-created_at", "-id").values("created_at")[:1]
    qs = Thread.objects.filter(user=user, mode=Thread.Mode.THREAD)
    if not include_archived:
        qs = qs.filter(archived_at__isnull=True)
    return qs.annotate(
        last_activity=Coalesce(Subquery(latest_message, output_field=DateTimeField()), 'created_at')
    ).order_by("-last_activity", "-created_at", "-id")


def group_threads_by_date(threads):
    """Group threads by date ranges: Today, Yesterday, Last Week, Last Month, Older"""
    now = timezone.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)
    last_month = today - timedelta(days=30)

    grouped = {
        'today': [],
        'yesterday': [],
        'last_week': [],
        'last_month': [],
        'older': []
    }

    for thread in threads:
        thread_date = (getattr(thread, 'last_activity', None) or thread.created_at).date()
        if thread_date == today:
            grouped['today'].append(thread)
        elif thread_date == yesterday:
            grouped['yesterday'].append(thread)
        elif thread_date > last_week:
            grouped['last_week'].append(thread)
        elif thread_date > last_month:
            grouped['last_month'].append(thread)
        else:
            grouped['older'].append(thread)

    return grouped


@ensure_csrf_cookie
@login_required(login_url='login')
def index(request):
    # Get initial MAX_THREADS_DISPLAYED threads
    # Hide the continuous thread from the classic Threads UI.
    include_archived = request.GET.get("archived") in {"1", "true", "yes"}
    threads = _classic_threads_for_user(
        request.user, include_archived=include_archived
    )[:MAX_THREADS_DISPLAYED]
    grouped_threads = group_threads_by_date(threads)
    total_count = _classic_threads_for_user(
        request.user, include_archived=include_archived
    ).count()

    return render(request, 'nova/index.html', {
        'grouped_threads': grouped_threads,
        'threads': threads,  # Keep for backward compatibility
        'has_more_threads': total_count > MAX_THREADS_DISPLAYED,
        'next_offset': MAX_THREADS_DISPLAYED,
        'include_archived': include_archived,
    })


@login_required(login_url='login')
def load_more_threads(request):
    """AJAX endpoint to load more threads"""
    offset = max(0, _parse_int(request.GET.get('offset'), 0))
    limit = _parse_int(request.GET.get('limit'), MAX_THREADS_DISPLAYED)

    include_archived = request.GET.get("archived") in {"1", "true", "yes"}
    limit = max(1, min(limit, 100))
    qs = _classic_threads_for_user(request.user, include_archived=include_archived)
    threads = qs[offset:offset + limit]
    grouped_threads = group_threads_by_date(threads)
    total_count = qs.count()

    # Render the grouped threads HTML
    html = render_to_string('nova/partials/_thread_groups.html', {
        'grouped_threads': grouped_threads
    }, request=request)

    return JsonResponse({
        'html': html,
        'has_more': (offset + limit) < total_count,
        'next_offset': offset + limit,
        'include_archived': include_archived,
    })


@csrf_protect
@login_required(login_url='login')
def message_list(request):
    user_agents, default_agent = get_message_panel_agents(
        request.user,
        thread_mode=Thread.Mode.THREAD,
        selected_agent_id=request.GET.get('agent_id'),
    )
    selected_thread_id = request.GET.get('thread_id')
    # With browser persistence removed, default to the most recent classic thread.
    if not selected_thread_id:
        latest = (
            Thread.objects.filter(user=request.user, mode=Thread.Mode.THREAD)
            .order_by('-created_at')
            .first()
        )
        if latest:
            selected_thread_id = str(latest.id)
    messages = None
    if selected_thread_id:
        try:
            selected_thread = get_object_or_404(Thread, id=selected_thread_id,
                                                user=request.user)
            message_limit = max(1, min(_parse_int(
                request.GET.get("limit"), MESSAGE_PAGE_SIZE
            ), 100))
            message_qs = selected_thread.get_messages().order_by("created_at", "id")
            total_messages = message_qs.count()
            message_offset = max(0, _parse_int(request.GET.get("offset"),
                                                max(0, total_messages - message_limit)))
            focus_message_id = request.GET.get("message_id", "")
            if focus_message_id and "offset" not in request.GET:
                focus_index = message_qs.filter(id=focus_message_id).values_list(
                    "created_at", "id"
                ).first()
                if focus_index:
                    message_offset = message_qs.filter(
                        Q(created_at__lt=focus_index[0])
                        | Q(created_at=focus_index[0], id__lt=focus_index[1])
                    ).count()
                    message_offset = max(0, message_offset - message_limit // 3)
            raw_messages = list(
                with_message_display_relations(
                    message_qs[message_offset:message_offset + message_limit]
                )
            )
            agent_config = get_user_default_agent(request.user)

            messages = prepare_messages_for_display(
                raw_messages,
                show_compact=getattr(selected_thread, 'mode', Thread.Mode.THREAD) == Thread.Mode.THREAD,
                compact_preserve_recent=(agent_config.preserve_recent if agent_config else None),
                render_system_summaries=True,
            )

            # Add pending interactions to context
            context = {
                'messages': messages,
                'thread_id': selected_thread_id,
                'user_agents': user_agents,
                'default_agent': default_agent,
                'pending_interactions': get_pending_interactions(selected_thread),
                'message_offset': message_offset,
                'message_limit': message_limit,
                'messages_has_more': message_offset > 0,
                'messages_next_offset': max(0, message_offset - message_limit),
                'focus_message_id': focus_message_id,
            }
            context.update(get_message_attachment_template_context())
            context.update(get_message_composer_template_context())
            return render(request, 'nova/message_container.html', context)

        except Http404:
            # Thread doesn't exist or user doesn't have access - return empty state
            selected_thread_id = None
            messages = None
        except Exception:
            logger.exception("Unexpected error while rendering message list for thread %s", selected_thread_id)
            selected_thread_id = None
            messages = None
    context = {
        'messages': messages,
        'thread_id': selected_thread_id or '',
        'user_agents': user_agents,
        'default_agent': default_agent,
        'message_offset': 0,
        'message_limit': MESSAGE_PAGE_SIZE,
        'messages_has_more': False,
        'messages_next_offset': 0,
        'focus_message_id': request.GET.get("message_id", ""),
    }
    context.update(get_message_attachment_template_context())
    context.update(get_message_composer_template_context())
    return render(request, 'nova/message_container.html', context)


def new_thread(request):
    count = Thread.objects.filter(user=request.user).count() + 1
    thread_subject = build_default_thread_subject(count)
    thread = Thread.objects.create(subject=thread_subject, user=request.user)
    thread_html = render_to_string('nova/partials/_thread_item.html',
                                   {'thread': thread}, request=request)
    return thread, thread_html


@require_POST
@login_required(login_url='login')
def create_thread(request):
    thread, thread_html = new_thread(request)
    return JsonResponse({"status": "OK", 'thread_id': thread.id,
                        'threadHtml': thread_html})


@require_POST
@login_required(login_url='login')
def delete_thread(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    try:
        delete_thread_for_user(thread, request.user)
    except ThreadDeletionError as exc:
        return JsonResponse({
            "status": "ERROR",
            "message": str(exc),
        }, status=exc.status_code)
    # JSON response (frontend uses fetch, and handles DOM removal).
    return JsonResponse({"status": "OK"})


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@require_POST
@login_required(login_url='login')
def archive_thread(request, thread_id):
    thread = get_object_or_404(
        Thread, id=thread_id, user=request.user, mode=Thread.Mode.THREAD
    )
    if thread.archived_at is None:
        thread.archived_at = timezone.now()
        thread.save(update_fields=["archived_at"])
    return JsonResponse({"status": "OK", "archived": True})


@require_POST
@login_required(login_url='login')
def unarchive_thread(request, thread_id):
    thread = get_object_or_404(
        Thread, id=thread_id, user=request.user, mode=Thread.Mode.THREAD
    )
    if thread.archived_at is not None:
        thread.archived_at = None
        thread.save(update_fields=["archived_at"])
    return JsonResponse({"status": "OK", "archived": False})


@csrf_protect
@require_POST
@login_required(login_url='login')
def add_message(request):
    thread_id = request.POST.get('thread_id')

    def prepare_context(message_text: str) -> SubmissionContext:
        if not thread_id or thread_id == 'None':
            thread, thread_html = new_thread(request)
        else:
            thread = get_object_or_404(Thread, id=thread_id, user=request.user)
            thread_html = None
        return SubmissionContext(
            thread=thread,
            create_message=lambda text: thread.add_message(text, actor=Actor.USER),
            thread_html=thread_html,
        )

    try:
        result = submit_user_message(
            user=request.user,
            submission_key=request.POST.get("submission_key"),
            message_text=request.POST.get('new_message', ''),
            selected_agent=request.POST.get('selected_agent'),
            response_mode=request.POST.get('response_mode'),
            thread_mode=Thread.Mode.THREAD,
            thread_files=request.FILES.getlist('files'),
            message_attachments=request.FILES.getlist('message_attachments'),
            prepare_context=prepare_context,
            dispatcher_task=run_ai_task_celery,
            thread_file_uploader=batch_upload_files,
            attachment_uploader=upload_message_attachments,
            file_update_publisher=publish_file_update,
        )
    except MessageSubmissionError as exc:
        return JsonResponse(
            {"status": "ERROR", "message": exc.message},
            status=exc.status_code,
        )

    return JsonResponse(result.as_payload())


@require_POST
@login_required(login_url='login')
def summarize_thread(request, thread_id):
    """Manually trigger conversation summarization for a thread."""
    # Verify thread exists and user has access
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)

    # Get the agent's summarization config
    agent_config = get_user_default_agent(request.user)
    if not agent_config:
        return JsonResponse({
            "status": "ERROR",
            "message": "No default agent configured"
        }, status=400)
    compaction_error = get_compaction_error(thread)
    if compaction_error:
        return JsonResponse({
            "status": "ERROR",
            "message": compaction_error,
        }, status=400)
    compactable_count = get_compactable_message_count(thread, agent_config)
    if compactable_count <= 0:
        min_messages_for_summarization = agent_config.preserve_recent + 1
        total_messages = (
            thread.get_messages()
            .exclude(actor=Actor.SYSTEM)
            .count()
        )
        return JsonResponse({
            "status": "ERROR",
            "message": (
                f"Not enough messages to summarize. Need at least "
                f"{min_messages_for_summarization} messages, but only have {total_messages}."
            )
        }, status=400)
    return start_summarization(request, thread, agent_config, False)


def start_summarization(request, thread, agent_config, include_sub_agents, sub_agent_ids=None):
    """Helper function to start summarization task."""
    task = Task.objects.create(
        user=request.user,
        thread=thread,
        agent_config=agent_config,
        status=TaskStatus.PENDING
    )

    # Trigger async summarization task
    summarize_thread_task.delay(
        thread.id,
        request.user.id,
        agent_config.id,
        task.id,
        include_sub_agents,
        sub_agent_ids or []
    )

    return JsonResponse({
        "status": "OK",
        "task_id": task.id,
        "message": "Thread summarization started."
    })


@require_POST
@login_required(login_url='login')
def confirm_summarize_thread(request, thread_id):
    """Handle user confirmation for sub-agent summarization."""
    import json
    include_sub_agents = request.POST.get('include_sub_agents') == 'true'
    sub_agent_ids = json.loads(request.POST.get('sub_agent_ids', '[]'))

    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    agent_config = get_user_default_agent(request.user)
    if not agent_config:
        return JsonResponse({
            "status": "ERROR",
            "message": "No default agent configured"
        }, status=400)
    compaction_error = get_compaction_error(thread)
    if compaction_error:
        return JsonResponse({
            "status": "ERROR",
            "message": compaction_error,
        }, status=400)
    return start_summarization(request, thread, agent_config, False)
