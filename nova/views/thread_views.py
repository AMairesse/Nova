# nova/views/thread_views.py
import bleach
from markdown import markdown
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from nova.models.models import Agent, UserProfile, Task, TaskStatus, UserFile
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.tasks import run_ai_task_celery
from nova.file_utils import ALLOWED_MIME_TYPES, MAX_FILE_SIZE
from django.conf import settings
import logging
import boto3
from botocore.exceptions import ClientError
import io  # For in-memory file handling
import uuid  # For unique keys

# Markdown configuration for better list handling
MARKDOWN_EXTENSIONS = [
    "extra",           # Basic extensions (tables, fenced code, etc.)
    "toc",             # Table of contents (includes better list processing)
    "sane_lists",      # Improved list handling
    "md_in_html",      # Allow markdown inside HTML
]

MARKDOWN_EXTENSION_CONFIGS = {
    'toc': {
        'marker': ''  # Disable TOC markers to avoid conflicts
    }
}

logger = logging.getLogger(__name__)

ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}


@ensure_csrf_cookie
@login_required(login_url='login')
def index(request):
    threads = Thread.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'nova/index.html', {'threads': threads})


@csrf_protect
@login_required(login_url='login')
def message_list(request):
    user_agents = Agent.objects.filter(user=request.user, is_tool=False)
    agent_id = request.GET.get('agent_id')
    default_agent = None
    if agent_id:
        default_agent = Agent.objects.filter(id=agent_id,
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
                raw_html = markdown(m.text,
                                    extensions=MARKDOWN_EXTENSIONS,
                                    extension_configs=MARKDOWN_EXTENSION_CONFIGS)
                clean_html = bleach.clean(raw_html,
                                          tags=ALLOWED_TAGS,
                                          attributes=ALLOWED_ATTRS,
                                          strip=True)
                m.rendered_html = mark_safe(clean_html)
                if m.actor == Actor.USER and m.internal_data and 'file_ids' in m.internal_data:
                    m.file_count = len(m.internal_data['file_ids'])
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
    s3_client = boto3.client('s3',
                             endpoint_url=settings.MINIO_ENDPOINT_URL,
                             aws_access_key_id=settings.MINIO_ACCESS_KEY,
                             aws_secret_access_key=settings.MINIO_SECRET_KEY)
    for file in uploaded_files:
        if file.size > MAX_FILE_SIZE:
            return JsonResponse({"status": "ERROR", "message": "File too large (max 10MB)"}, status=400)
        if file.content_type not in ALLOWED_MIME_TYPES:
            return JsonResponse({"status": "ERROR", "message": "Unsupported file type"}, status=400)
        try:
            unique_id = uuid.uuid4().hex[:8]
            key = f"{request.user.id}/{thread.id}/{unique_id}_{file.name}"
            s3_client.upload_fileobj(io.BytesIO(file.read()),
                                     settings.MINIO_BUCKET_NAME, key,
                                     ExtraArgs={'ContentType': file.content_type})
            user_file = UserFile.objects.create(user=request.user,
                                                thread=thread,
                                                key=key,
                                                original_filename=file.name,
                                                mime_type=file.content_type,
                                                size=file.size)
            uploaded_file_ids.append(user_file.id)
        except ClientError as e:
            logger.error(f"Upload failed: {e}")
            return JsonResponse({"status": "ERROR", "message": "File upload failed"}, status=500)

    message = thread.add_message(new_message, actor=Actor.USER)
    message.internal_data = {'file_ids': uploaded_file_ids}
    message.save()

    agent_config = None
    if selected_agent:
        agent_config = get_object_or_404(Agent, id=selected_agent,
                                         user=request.user)
    else:
        try:
            agent_config = request.user.userprofile.default_agent
        except UserProfile.DoesNotExist:
            pass

    task = Task.objects.create(
        user=request.user, thread=thread,
        agent=agent_config, status=TaskStatus.PENDING
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
