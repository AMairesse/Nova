# nova/views/thread_views.py
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
from nova.models.AgentConfig import AgentConfig
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.UserObjects import UserProfile
from nova.tasks.tasks import run_ai_task_celery, compact_conversation_celery
from nova.utils import markdown_to_html
import logging

from asgiref.sync import async_to_sync
from nova.file_utils import batch_upload_files

logger = logging.getLogger(__name__)

MAX_THREADS_DISPLAYED = 10


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
        thread_date = thread.created_at.date()
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
    threads = Thread.objects.filter(user=request.user).order_by('-created_at')[:MAX_THREADS_DISPLAYED]
    grouped_threads = group_threads_by_date(threads)
    total_count = Thread.objects.filter(user=request.user).count()

    return render(request, 'nova/index.html', {
        'grouped_threads': grouped_threads,
        'threads': threads,  # Keep for backward compatibility
        'has_more_threads': total_count > MAX_THREADS_DISPLAYED,
        'next_offset': MAX_THREADS_DISPLAYED
    })


@login_required(login_url='login')
def load_more_threads(request):
    """AJAX endpoint to load more threads"""
    offset = int(request.GET.get('offset', 0))
    limit = int(request.GET.get('limit', MAX_THREADS_DISPLAYED))

    threads = Thread.objects.filter(user=request.user).order_by('-created_at')[offset:offset + limit]
    grouped_threads = group_threads_by_date(threads)
    total_count = Thread.objects.filter(user=request.user).count()

    # Render the grouped threads HTML
    html = render_to_string('nova/partials/_thread_groups.html', {
        'grouped_threads': grouped_threads
    }, request=request)

    return JsonResponse({
        'html': html,
        'has_more': (offset + limit) < total_count,
        'next_offset': offset + limit
    })


@csrf_protect
@login_required(login_url='login')
def message_list(request):
    user_agents = AgentConfig.objects.filter(user=request.user, is_tool=False)
    agent_id = request.GET.get('agent_id')
    default_agent = None
    if agent_id:
        default_agent = AgentConfig.objects.filter(id=agent_id,
                                                   user=request.user).first()
    if not default_agent:
        default_agent = getattr(request.user.userprofile,
                                "default_agent", None)
    selected_thread_id = request.GET.get('thread_id')
    messages = None
    if selected_thread_id:
        try:
            selected_thread = get_object_or_404(Thread, id=selected_thread_id,
                                                user=request.user)
            messages = selected_thread.get_messages()
            for m in messages:
                m.rendered_html = markdown_to_html(m.text)
                # Add info about files used
                if m.actor == Actor.USER and m.internal_data and 'file_ids' in m.internal_data:
                    m.file_count = len(m.internal_data['file_ids'])
                # Process summary from markdown to HTML
                if m.actor == Actor.SYSTEM and m.internal_data and 'summary' in m.internal_data:
                    m.internal_data['summary'] = markdown_to_html(m.internal_data['summary'])

            # Fetch pending interactions for server-side rendering
            pending_interactions = Interaction.objects.filter(
                thread=selected_thread,
                status=InteractionStatus.PENDING
            ).select_related('task', 'agent_config')

            # Add pending interactions to context
            context = {
                'messages': messages,
                'thread_id': selected_thread_id,
                'user_agents': user_agents,
                'default_agent': default_agent,
                'pending_interactions': pending_interactions,
            }
            return render(request, 'nova/message_container.html', context)

        except Exception:
            # Thread doesn't exist or user doesn't have access - return empty state
            selected_thread_id = None
            messages = None
    return render(request, 'nova/message_container.html', {
        'messages': messages,
        'thread_id': selected_thread_id or '',
        'user_agents': user_agents,
        'default_agent': default_agent
    })


def new_thread(request):
    count = Thread.objects.filter(user=request.user).count() + 1
    thread_subject = f"thread n°{count}"
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
    thread.delete()
    return redirect('index')


@csrf_protect
@require_POST
@login_required(login_url='login')
def add_message(request):
    thread_id = request.POST.get('thread_id')
    new_message = request.POST.get('new_message', '')
    selected_agent = request.POST.get('selected_agent')
    uploaded_files = request.FILES.getlist('files', [])

    if not thread_id or thread_id == 'None':
        thread, thread_html = new_thread(request)
    else:
        thread = get_object_or_404(Thread, id=thread_id, user=request.user)
        thread_html = None

    uploaded_file_ids = []

    if uploaded_files:
        # Prepare data for unified async upload pipeline.
        # We propose simple top-level paths; batch_upload_files() will:
        # - sanitize paths
        # - enforce size and MIME limits
        # - auto-rename on collision
        # - upload to MinIO under users/{user_id}/threads/{thread_id}{safe_path}
        file_data = []
        for f in uploaded_files:
            try:
                content = f.read()
            except Exception as e:
                logger.error(f"Failed reading uploaded file {f.name}: {e}")
                return JsonResponse(
                    {"status": "ERROR", "message": "File upload failed while reading content"},
                    status=500,
                )
            # Use a simple POSIX path; sanitize_user_path() will normalize.
            proposed_path = f"/{f.name}"
            file_data.append({"path": proposed_path, "content": content})

        try:
            created_files, errors = async_to_sync(batch_upload_files)(thread, request.user, file_data)
        except Exception as e:
            logger.error(f"Batch upload failed: {e}")
            return JsonResponse(
                {"status": "ERROR", "message": "File upload failed"},
                status=500,
            )

        # Collect created ids for message.internal_data and API compatibility.
        for item in created_files:
            file_id = item.get("id")
            if file_id:
                uploaded_file_ids.append(file_id)

        # Surface validation errors (size, MIME, etc.) in a backward compatible way:
        # if any error occurred and nothing was uploaded, treat as failure.
        if errors and not uploaded_file_ids:
            # Join errors into a single message; details are safe/validation oriented.
            return JsonResponse(
                {"status": "ERROR", "message": "; ".join(errors)},
                status=400,
            )

    message = thread.add_message(new_message, actor=Actor.USER)
    message.internal_data = {'file_ids': uploaded_file_ids}
    message.save()

    agent_config = None
    if selected_agent:
        agent_config = get_object_or_404(AgentConfig, id=selected_agent,
                                         user=request.user)
    else:
        try:
            agent_config = request.user.userprofile.default_agent
        except UserProfile.DoesNotExist:
            pass

    task = Task.objects.create(
        user=request.user, thread=thread,
        agent_config=agent_config, status=TaskStatus.PENDING
    )

    run_ai_task_celery.delay(task.id, request.user.id, thread.id, agent_config.id if agent_config else None, message.id)

    # Prepare message data for JSON response
    message_data = {
        "id": message.id,
        "text": new_message,  # Return raw text for client-side rendering
        "actor": message.actor,
        "file_count": len(uploaded_file_ids) if uploaded_file_ids else 0,
        "internal_data": message.internal_data or {}
    }

    return JsonResponse({
        "status": "OK",
        "message": message_data,
        "thread_id": thread.id,
        "task_id": task.id,
        "threadHtml": thread_html,
        "uploaded_file_ids": uploaded_file_ids
    })


@require_POST
@login_required(login_url='login')
def compact_thread(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)

    # Get agent (default or from profile)
    try:
        agent_config = request.user.userprofile.default_agent
    except UserProfile.DoesNotExist:
        agent_config = None

    # Create task
    task = Task.objects.create(
        user=request.user,
        thread=thread,
        agent_config=agent_config,
        status=TaskStatus.PENDING
    )

    # Queue task (system message will be added by CompactTaskExecutor after completion)
    compact_conversation_celery.delay(task.id, request.user.id, thread.id, agent_config.id if agent_config else None)

    return JsonResponse({
        'status': 'queued',
        'task_id': task.id
    })
