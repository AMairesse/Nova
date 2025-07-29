# nova/views/main_views.py
import bleach
import threading, asyncio
import datetime as dt
from uuid import UUID
from typing import Any, Dict, List, Optional
from markdown import markdown
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from ..models import Actor, Thread, Agent, UserProfile, Task, TaskStatus
from ..llm_agent import LLMAgent
from channels.layers import get_channel_layer
from langchain_core.callbacks import AsyncCallbackHandler

import logging
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

    # Return immediately with task_id for client-side WS connection
    return JsonResponse({
        "status": "OK",
        "thread_id": thread.id,
        "task_id": task.id,  # Client uses this for WS
        "threadHtml": thread_html
    })

# Custom callback handler for synthesis and streaming (signatures aligned with Langchain)
class TaskProgressHandler(AsyncCallbackHandler):
    def __init__(self, task_id, channel_layer):
        self.task_id = task_id
        self.channel_layer = channel_layer
        self.final_chunks = []
        self.current_tool = None

    async def publish_update(self, message_type, data):
        await self.channel_layer.group_send(
            f'task_{self.task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    # Implement needed callbacks from
    # https://python.langchain.com/docs/concepts/callbacks/
    async def on_chat_model_start(self, serialized: Dict[str, Any], messages: List[Any], **kwargs: Any) -> Any:
        try:
            await self.publish_update('progress_update', {'progress_log': "Chat model started"})
        except Exception as e:
            logger.error(f"Error in on_chat_model_start: {e}")

    async def on_llm_new_token(self, token: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None, **kwargs: Any) -> Any:
        #TODO: filter sub agents but use it as a progress update
        try:
            self.final_chunks.append(token)
            await self.publish_update('response_chunk', {'chunk': token})
        except Exception as e:
            logger.error(f"Error in on_llm_new_token: {e}")
    
    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        try:
            tool_name = serialized.get('name', 'Unknown')
            self.current_tool = tool_name
            await self.publish_update('progress_update', {'progress_log': f"Calling tool: {tool_name}"})
        except Exception as e:
            logger.error(f"Error in on_tool_start: {e}")

    async def on_tool_end(self, output: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None, **kwargs: Any) -> Any:
        try:
            if self.current_tool:
                await self.publish_update('progress_update', {'progress_log': f"{self.current_tool} finished with result: {output[:50]}..."})
                self.current_tool = None
        except Exception as e:
            logger.error(f"Error in on_tool_end: {e}")
    
    async def on_agent_finish(self, finish: Any, *, run_id: UUID, parent_run_id: Optional[UUID] = None, **kwargs: Any) -> Any:
        pass


# Updated helper function for background thread
def run_ai_task(task_id, user_id, thread_id, agent_id):
    """
    Background function to run AI task in a thread.
    Uses custom callbacks for progress synthesis and streaming.
    """
    from django.contrib.auth.models import User  # Import inside to avoid circular issues
    channel_layer = get_channel_layer()  # Get layer for publishing

    try:
        task = Task.objects.get(id=task_id)
        user = User.objects.get(id=user_id)
        thread = Thread.objects.get(id=thread_id)
        agent_obj = Agent.objects.get(id=agent_id) if agent_id else None

        # Set task to running and log start
        task.status = TaskStatus.RUNNING
        task.progress_logs = [{"step": "Starting AI processing", "timestamp": str(dt.datetime.now(dt.timezone.utc))}]
        task.save()

        # Get message history
        messages = thread.get_messages()
        msg_history = [[m.actor, m.text] for m in messages]
        if msg_history:
            msg_history.pop()  # Exclude last user message for consistency
        # Last message is the prompt to send
        last_message = messages.last().text

        # Create custom handler and LLMAgent with callbacks
        handler = TaskProgressHandler(task_id, channel_layer)
        llm = LLMAgent(user, thread_id, msg_history=msg_history, agent=agent_obj, callbacks=[handler])

        # Run LLMAgent
        final_output = llm.invoke(last_message)

        # Send a final progress update for the LLM response
        # This will be used for closing the status display of the task
        asyncio.run(handler.publish_update('task_complete', {'status': "RESPONSE_COMPLETED", 'result': final_output}))

        # Log completion and save result
        task.progress_logs.append({"step": "AI processing completed", "timestamp": str(dt.datetime.now(dt.timezone.utc))})
        task.result = final_output

        # Add final message to thread
        thread.add_message(final_output, actor=Actor.AGENT)

        # Update thread subject if needed
        if thread.subject.startswith("thread n°"):
            short_title = llm.invoke(
                "Give me a short title for this conversation (1–3 words maximum).\
                 Use the same language as the conversation.\
                 Answer by giving only the title, nothing else.",
                silent_mode=True
            )
            thread.subject = short_title.strip()
            thread.save()

        # Send a final progress update for task
        # This will be used for updating the thread name and closing the socket
        asyncio.run(handler.publish_update('task_complete', {'status': "TASK_COMPLETED", 'result': final_output}))
        task.status = TaskStatus.COMPLETED
        task.save()

    except Exception as e:
        # Handle failure
        task.status = TaskStatus.FAILED
        task.result = f"Error: {str(e)}"
        task.progress_logs.append({"step": f"Error occurred: {str(e)}", "timestamp": str(dt.datetime.now(dt.timezone.utc))})
        task.save()
        logger.error(f"Task {task_id} failed: {e}")

@login_required
def running_tasks(request, thread_id):
    """
    JSON endpoint to get running task IDs for a thread.
    Returns list of task_ids in 'RUNNING' status for the current user.
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    running_ids = Task.objects.filter(
        thread=thread,
        user=request.user,
        status=TaskStatus.RUNNING
    ).values_list('id', flat=True)
    return JsonResponse({'running_task_ids': list(running_ids)})
