# nova/views/main_views.py
import json
import re
import bleach
import threading
import datetime as dt
from markdown import markdown
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from ..models import Actor, Thread, Agent, UserProfile, Task, TaskStatus
from ..llm_agent import LLMAgent
from ..utils import extract_final_answer


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
        default_agent = Agent.objects.filter(id=agent_id, user=request.user).first()
    if not default_agent:
        default_agent = getattr(request.user.userprofile, "default_agent", None)

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

    if not thread_id or thread_id == 'None':
        # New thread
        thread, thread_html = new_thread(request)
    else:
        thread       = Thread.objects.get(id=thread_id)
        thread_html  = None

    # Add the user message to the thread
    thread.add_message(new_message, actor=Actor.USER)

    # Get the agent object
    agent_obj = None
    if selected_agent:
        agent_obj = get_object_or_404(Agent, id=selected_agent, user=request.user)
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
    threading.Thread(target=run_ai_task, args=(task.id, request.user.id, thread.id, agent_obj.id if agent_obj else None)).start()

    # Return immediately with task_id for client-side polling (in later steps)
    return JsonResponse({
        "status": "OK",
        "thread_id": thread.id,
        "task_id": task.id,  # Client can use this to poll for updates
        "threadHtml": thread_html
    })

@login_required
def task_detail(request, task_id):
    """
    JSON endpoint to get task details for polling.
    Returns status, logs, result, and timestamps.
    """
    task = get_object_or_404(Task, id=task_id, user=request.user)  # Ensure ownership
    return JsonResponse({
        'id': task.id,
        'status': task.status,
        'progress_logs': task.progress_logs,  # JSON list of steps
        'result': task.result,
        'created_at': task.created_at.isoformat(),
        'updated_at': task.updated_at.isoformat(),
        'is_completed': task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED],
    })


# Updated helper function for background thread
def run_ai_task(task_id, user_id, thread_id, agent_id):
    """
    Background function to run AI task in a thread.
    Uses stream_events to capture progress, logs each event to Task.progress_logs,
    and handles final output.
    """
    from django.contrib.auth.models import User  # Import inside to avoid circular issues
    try:
        task = Task.objects.get(id=task_id)
        user = User.objects.get(id=user_id)
        thread = Thread.objects.get(id=thread_id)
        agent_obj = Agent.objects.get(id=agent_id) if agent_id else None

        # Set task to running and log start
        task.status = TaskStatus.RUNNING
        task.progress_logs.append({
            "step": "Starting AI processing",
            "timestamp": str(dt.datetime.now(dt.timezone.utc))
        })
        task.save()

        # Get message history
        messages = thread.get_messages()
        msg_history = [[m.actor, m.text] for m in messages]
        if msg_history:
            msg_history.pop()  # Exclude last user message for consistency
        # Last message is the prompt to send
        last_message = messages.last().text

        # Create LLMAgent
        llm = LLMAgent(user, thread_id, msg_history=msg_history, agent=agent_obj)

        # Function to map events to log dicts (simplified from map_event)
        def map_event_to_log(ev: dict):
            evt = ev["event"]
            depth = len(ev.get("parent_ids", []))
            name = ev["name"]
            kind = "agent" if evt.startswith("on_chain") else "tool"
            timestamp = str(dt.datetime.now(dt.timezone.utc))

            if evt.endswith("_start"):
                return {"event": "start", "kind": kind, "name": name, "depth": depth, "timestamp": timestamp}
            if evt.endswith("_stream"):
                chunk = extract_final_answer(ev["data"].get("chunk", ""))
                if not chunk:
                    return None
                return {"event": "stream", "kind": kind, "name": name, "depth": depth, "chunk": chunk, "timestamp": timestamp}
            if evt.endswith("_end"):
                output = extract_final_answer(ev["data"].get("output", ""))
                return {"event": "end", "kind": kind, "name": name, "depth": depth, "output": output, "timestamp": timestamp}
            return None

        # Stream events and log progress
        final_output = ""
        for ev in llm.stream_events(last_message):
            log_entry = map_event_to_log(ev)
            if log_entry:
                task.progress_logs.append(log_entry)
                task.save()  # Save incrementally for real-time persistence

            # Capture final output at root depth
            if log_entry and log_entry["event"] == "end" and log_entry["depth"] == 0:
                final_output = log_entry.get("output", "")

        # Log completion and save result
        task.progress_logs.append({
            "step": "AI processing completed",
            "timestamp": str(dt.datetime.now(dt.timezone.utc))
        })
        task.result = final_output
        task.status = TaskStatus.COMPLETED
        task.save()

        # Add final message to thread
        if final_output:
            thread.add_message(final_output, actor=Actor.AGENT)

        # Update thread subject if needed (similar to original)
        if thread.subject.startswith("thread n°"):
            short_title = llm.invoke(
                "Give me a short title for this conversation (1–3 words maximum)."
                "Use the same language as the conversation."
                "Answer by giving only the title, nothing else."
            )
            thread.subject = short_title.strip()
            thread.save()

    except Exception as e:
        # Handle failure
        task.status = TaskStatus.FAILED
        task.result = f"Error: {str(e)}"
        task.progress_logs.append({
            "step": f"Error occurred: {str(e)}",
            "timestamp": str(dt.datetime.now(dt.timezone.utc))
        })
        task.save()
