# nova/views/main_views.py
import bleach
import threading
import datetime as dt
from markdown import markdown
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from ..models import Actor, Thread, Agent, UserProfile, Task, TaskStatus, UserFile
from ..tasks import sync_run_ai_task  # Import from new tasks.py
from django.conf import settings
import logging
import boto3
from botocore.exceptions import ClientError
import io  # For in-memory file handling
import uuid  # For unique keys

logger = logging.getLogger(__name__)

ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}

ALLOWED_MIME_TYPES = ['image/jpeg', 'image/png', 'text/plain', 'text/html', 'application/pdf', 'application/msword']  # Whitelist
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

@ensure_csrf_cookie
@login_required(login_url='login')
def index(request):
    # Load all threads for this user.
    threads = Thread.objects.filter(user=request.user).order_by('-created_at')

    return render(request, 'nova/index.html', {
        'threads': threads,
    })

@csrf_protect
@login_required(login_url='login')
def message_list(request):
    """
    Ajax endpoint returning the partial HTML snippet (message_container.html)
    for a given thread.
    """
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
        # Check ownership
        selected_thread = get_object_or_404(
            Thread, id=selected_thread_id, user=request.user
        )
        # Get the messages
        messages = selected_thread.get_messages()
        for m in messages:
            raw_html = markdown(m.text, extensions=["extra"])
            clean_html = bleach.clean(
                raw_html,
                tags=ALLOWED_TAGS,
                attributes=ALLOWED_ATTRS,
                strip=True,
            )
            m.rendered_html = mark_safe(clean_html)
            
            # ----- Ajout rendu fichiers -----
            if m.actor == Actor.USER and m.internal_data and 'file_ids' in m.internal_data:
                file_urls = []
                for fid in m.internal_data['file_ids']:
                    file = get_object_or_404(UserFile, id=fid, user=request.user)
                    file_urls.append({'name': file.original_filename, 'url': file.get_download_url()})
                m.file_attachments = file_urls  # Passe au template

    return render(request, 'nova/message_container.html', {
        'messages': messages,
        'thread_id': selected_thread_id or '',
        'user_agents': user_agents,
        'default_agent': default_agent
    })

def new_thread(request):
    count = Thread.objects.filter(user=request.user).count() + 1
    thread_subject = f"thread n°{count}"  # Fixed encoding
    thread = Thread.objects.create(subject=thread_subject, user=request.user)

    # Render the thread item template
    thread_html = render_to_string('nova/partials/_thread_item.html',
                                   {'thread': thread},
                                   request=request)

    return thread, thread_html

@require_POST
@login_required(login_url='login')
def create_thread(request):
    thread, thread_html = new_thread(request)

    return JsonResponse({
        "status": "OK",
        'thread_id': thread.id,
        'threadHtml': thread_html
    })

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
    uploaded_files = request.FILES.getlist('files', [])  # Support multi-files

    if not thread_id or thread_id == 'None':
        # New thread
        thread, thread_html = new_thread(request)
    else:
        thread = get_object_or_404(Thread, id=thread_id, user=request.user)  # Fixed ownership check
        thread_html = None

    # Handle file uploads natively with validation
    uploaded_file_ids = []
    s3_client = boto3.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    )
    for file in uploaded_files:
        if file.size > MAX_FILE_SIZE:
            return JsonResponse({"status": "ERROR", "message": "File too large (max 10MB)"}, status=400)
        if file.content_type not in ALLOWED_MIME_TYPES:
            return JsonResponse({"status": "ERROR", "message": "Unsupported file type"}, status=400)
        
        try:
            # Generate unique key to avoid collisions
            unique_id = uuid.uuid4().hex[:8]
            key = f"{request.user.id}/{thread.id}/{unique_id}_{file.name}"
            s3_client.upload_fileobj(
                io.BytesIO(file.read()),  # In-memory to avoid temp files
                settings.MINIO_BUCKET_NAME,
                key,
                ExtraArgs={'ContentType': file.content_type}
            )
            user_file = UserFile.objects.create(
                user=request.user,
                thread=thread,
                key=key,
                original_filename=file.name,
                mime_type=file.content_type,
                size=file.size
            )
            uploaded_file_ids.append(user_file.id)
        except ClientError as e:
            logger.error(f"Upload failed: {e}")
            return JsonResponse({"status": "ERROR", "message": "File upload failed"}, status=500)

    # Add the user message to the thread (append file info if any)
    message = thread.add_message(new_message, actor=Actor.USER)
    message.internal_data = {'file_ids': uploaded_file_ids}
    message.save()

    # Get the agent object
    agent_obj = None
    if selected_agent:
        agent_obj = get_object_or_404(Agent, id=selected_agent,
                                      user=request.user)
    else:
        try:
            agent_obj = request.user.userprofile.default_agent
        except UserProfile.DoesNotExist:
            pass  # Proceed without agent if none set

    # Create a Task for async processing
    task = Task.objects.create(
        user=request.user,
        thread=thread,
        agent=agent_obj,
        status=TaskStatus.PENDING
    )

    # Launch background thread to run the AI task
    threading.Thread(target=sync_run_ai_task, args=(task.id, request.user.id,
                     thread.id, agent_obj.id if agent_obj else None)).start()

    # Return immediately with task_id for client-side WS connection
    return JsonResponse({
        "status": "OK",
        "thread_id": thread.id,
        "task_id": task.id,  # Client uses this for WS
        "threadHtml": thread_html,
        "uploaded_file_ids": uploaded_file_ids
    })

@login_required
def running_tasks(request, thread_id):
    """
    JSON endpoint to get running task IDs for a thread.
    Returns list of task_ids in 'RUNNING' status for the current user.
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    # Extra ownership check (fix anomaly)
    if thread.user != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    running_ids = Task.objects.filter(
        thread=thread,
        user=request.user,
        status=TaskStatus.RUNNING
    ).values_list('id', flat=True)
    return JsonResponse({'running_task_ids': list(running_ids)})
