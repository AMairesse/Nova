# nova/views/main_views.py
import json
import re
import bleach
import threading
import asyncio
import datetime as dt
from uuid import UUID  # Correction: Pour signatures Langchain
from typing import Any, Dict, List, Optional  # Pour signatures
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
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage  # Import pour astream

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

@database_sync_to_async
def get_task_state(task_id):
    """Sync helper to get task state for publishing."""
    try:
        task = Task.objects.get(id=task_id)  # Note: No user check here as it's internal; consumer handles auth
        return {
            'status': task.status,
            'progress_logs': task.progress_logs,
            'result': task.result,
            'updated_at': task.updated_at.isoformat(),
            'is_completed': task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED],
        }
    except Task.DoesNotExist:
        return {'error': 'Task not found'}

# Custom callback handler for synthesis and streaming (signatures aligned with Langchain)
class TaskProgressHandler(AsyncCallbackHandler):
    def __init__(self, task_id, channel_layer):
        self.task_id = task_id
        self.channel_layer = channel_layer
        self.sync_publish = async_to_sync(self.publish_update)
        self.progress_logs = []  # In-memory for synthesis
        self.final_chunks = []  # Collect final response chunks
        self.current_tool = None

    async def publish_update(self, message_type, data):
        logger.debug(f"Publishing {message_type}: {data}")  # Debug
        await self.channel_layer.group_send(
            f'task_{self.task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    # Implémentation vide pour éviter NotImplementedError
    async def on_chat_model_start(self, serialized: Dict[str, Any], messages: List[List[Any]], *, run_id: UUID, parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        logger.debug(f"Ignoring on_chat_model_start for run_id: {run_id}")  # Debug
        pass

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        try:
            tool_name = serialized.get('name', 'Unknown')
            self.current_tool = tool_name
            log = {"timestamp": str(dt.datetime.now(dt.timezone.utc)), "step": f"Appel tool: {tool_name}"}
            self.progress_logs.append(log)
            if len(self.progress_logs) > 10:  # Limit FIFO
                self.progress_logs.pop(0)
            await self.publish_update('progress_update', {'progress_logs': self.progress_logs})
        except Exception as e:
            logger.error(f"Error in on_tool_start: {e}")

    async def on_tool_end(self, output: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None, **kwargs: Any) -> Any:
        try:
            if self.current_tool:
                log = {"timestamp": str(dt.datetime.now(dt.timezone.utc)), "step": f"Retour: {output[:50]}..."}  # Simplified result
                self.progress_logs.append(log)
                if len(self.progress_logs) > 10:
                    self.progress_logs.pop(0)
                await self.publish_update('progress_update', {'progress_logs': self.progress_logs})
                self.current_tool = None
        except Exception as e:
            logger.error(f"Error in on_tool_end: {e}")

    async def on_chain_stream(self, event: Any, run_id: UUID, *, parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        try:
            logger.debug(f"on_chain_stream called with event: {event}")  # Debug: Dump event pour voir structure
            # Only process root events
            if parent_run_id is None:
                data = event.get('data', {})
                chunk_data = data.get('chunk', {})
                # Filtrage flexible: Cherche 'content' dans messages ou output
                if 'messages' in chunk_data:
                    for msg in chunk_data['messages']:
                        if hasattr(msg, 'content') and msg.content:
                            chunk = msg.content
                            self.final_chunks.append(chunk)
                            await self.publish_update('response_chunk', {'chunk': chunk})
                            logger.debug(f"Streamed chunk: {chunk}")
                            return
                elif 'output' in chunk_data and chunk_data['output']:
                    chunk = extract_final_answer(chunk_data['output'])
                    if chunk:
                        self.final_chunks.append(chunk)
                        await self.publish_update('response_chunk', {'chunk': chunk})
        except Exception as e:
            logger.error(f"Error in on_chain_stream: {e}")

    async def on_chain_end(self, outputs: Dict[str, Any], *, run_id: UUID, parent_run_id: Optional[UUID] = None, **kwargs: Any) -> Any:
        try:
            logger.debug(f"on_chain_end called with outputs: {outputs}")  # Debug
            # Only process root events
            if parent_run_id is None:
                # Fallback: Capture output final si pas streamé
                final_output = extract_final_answer(outputs) if outputs else ''.join(self.final_chunks)
                if final_output and not self.final_chunks:  # Si pas de chunks, append ici
                    self.final_chunks = [final_output]  # Pour cohérence
                await self.publish_update('task_complete', {'result': final_output, 'status': 'COMPLETED'})
        except Exception as e:
            logger.error(f"Error in on_chain_end: {e}")

# Updated helper function for background thread
def run_ai_task(task_id, user_id, thread_id, agent_id):
    """
    Background function to run AI task in a thread.
    Uses custom callbacks for progress synthesis and streaming.
    """
    from django.contrib.auth.models import User  # Import inside to avoid circular issues
    channel_layer = get_channel_layer()  # Get layer for publishing

    async def async_publish_update(message_type, data):
        """Async function to publish task state to group."""
        await channel_layer.group_send(
            f'task_{task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    # Wrap the async function to make it callable from sync context
    sync_publish = async_to_sync(async_publish_update)

    try:
        task = Task.objects.get(id=task_id)
        user = User.objects.get(id=user_id)
        thread = Thread.objects.get(id=thread_id)
        agent_obj = Agent.objects.get(id=agent_id) if agent_id else None

        # Set task to running and log start
        task.status = TaskStatus.RUNNING
        task.progress_logs = [{"step": "Starting AI processing", "timestamp": str(dt.datetime.now(dt.timezone.utc))}]
        task.save()
        sync_publish('progress_update', {'progress_logs': task.progress_logs})  # Initial update

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

        # Consume astream to trigger callbacks (avec gestion erreurs globale)
        async def consume_astream():
            try:
                async for _ in llm.astream(last_message):  # Itère pour déclencher events/callbacks
                    pass  # Pas besoin des yields, callbacks gèrent
            except Exception as e:
                logger.error(f"Error consuming astream: {e}")
                # Publish failure si crash
                await handler.publish_update('task_complete', {'result': str(e), 'status': 'FAILED'})

        asyncio.run(consume_astream())  # Lance et consomme dans sync thread

        # Log completion and save result (use full final_output)
        task.progress_logs.append({"step": "AI processing completed", "timestamp": str(dt.datetime.now(dt.timezone.utc))})
        task.result = ''.join(handler.final_chunks) if handler.final_chunks else ""  # Full join
        task.status = TaskStatus.COMPLETED
        task.save()
        logger.debug(f"Task {task_id} completed with result: {task.result[:50]}...")  # Debug

        # Add final message to thread
        if task.result:
            thread.add_message(task.result, actor=Actor.AGENT)

        # Update thread subject if needed
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
        task.progress_logs.append({"step": f"Error occurred: {str(e)}", "timestamp": str(dt.datetime.now(dt.timezone.utc))})
        task.save()
        sync_publish('task_complete', {'result': task.result, 'status': 'FAILED'})  # Publish failure
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
