# nova/tasks.py
import datetime as dt
from uuid import UUID
from typing import Any, Dict, List, Optional
from markdown import markdown
import bleach
from channels.layers import get_channel_layer
from langchain_core.callbacks import AsyncCallbackHandler
from asgiref.sync import sync_to_async
from nova.models.models import TaskStatus, Task, Agent
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.llm.llm_agent import LLMAgent
import logging

logger = logging.getLogger(__name__)

ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}


# Custom callback handler for synthesis and streaming
class TaskProgressHandler(AsyncCallbackHandler):
    def __init__(self, task_id, channel_layer):
        self.task_id = task_id
        self.channel_layer = channel_layer
        self.final_chunks = []
        self.current_tool = None
        self.tool_depth = 0
        self.token_count = 0

    async def publish_update(self, message_type, data):
        await self.channel_layer.group_send(
            f'task_{self.task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    async def on_chain_start(self, serialized: Dict[str, Any],
                             inputs: Dict[str, Any], *, run_id: UUID,
                             parent_run_id: Optional[UUID] = None,
                             tags: Optional[List[str]] = None,
                             metadata: Optional[Dict[str, Any]] = None,
                             **kwargs: Any) -> None:
        pass

    async def on_chain_end(self, outputs: Dict[str, Any], *,
                           run_id: UUID, parent_run_id: Optional[UUID] = None,
                           tags: Optional[List[str]] = None,
                           **kwargs: Any) -> None:
        pass

    async def on_chat_model_start(self, serialized: Dict[str, Any],
                                  messages: List[Any], **kwargs: Any) -> Any:
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent started"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent started"})
        except Exception as e:
            logger.error(f"Error in on_chat_model_start: {e}")

    async def on_llm_start(self, serialized: Dict[str, Any],
                           prompts: List[str], **kwargs: Any):
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent started"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent started"})
        except Exception as e:
            logger.error(f"Error in on_llm_end: {e}")

    async def on_llm_new_token(self, token: str, *, run_id: UUID,
                               parent_run_id: Optional[UUID] = None,
                               **kwargs: Any) -> Any:
        try:
            # Send only chunks from the root run
            if self.tool_depth == 0:
                self.final_chunks.append(token)
                full_response = ''.join(self.final_chunks)
                raw_html = markdown(full_response, extensions=["extra"])
                clean_html = bleach.clean(
                    raw_html,
                    tags=ALLOWED_TAGS,
                    attributes=ALLOWED_ATTRS,
                    strip=True,
                )
                await self.publish_update('response_chunk',
                                          {'chunk': clean_html})
            else:
                # If a sub agent is generating a response,
                # send it as a progress update every 100 tokens
                self.token_count += 1
                if self.token_count % 100 == 0:
                    await self.publish_update('progress_update',
                                              {'progress_log':
                                               "Sub-agent still working..."})
        except Exception as e:
            logger.error(f"Error in on_llm_new_token: {e}")

    async def on_llm_end(self, response: Any, *, run_id: UUID,
                         parent_run_id: Optional[UUID] = None,
                         **kwargs: Any) -> Any:
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent finished"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent finished"})
        except Exception as e:
            logger.error(f"Error in on_llm_end: {e}")

    async def on_tool_start(self, serialized: Dict[str, Any],
                            input_str: str, *, run_id: UUID,
                            parent_run_id: Optional[UUID] = None,
                            tags: Optional[List[str]] = None,
                            metadata: Optional[Dict[str, Any]] = None,
                            **kwargs: Any) -> Any:
        try:
            # If a tool is starting,
            # store it to avoid sending response chunks back to the user
            tool_name = serialized.get('name', 'Unknown')
            self.current_tool = tool_name
            self.tool_depth += 1
            await self.publish_update('progress_update',
                                      {'progress_log':
                                       f"Tool '{tool_name}' started"})
        except Exception as e:
            logger.error(f"Error in on_tool_start: {e}")

    async def on_tool_end(self, output: Any, *, run_id: UUID,
                          parent_run_id: Optional[UUID] = None,
                          **kwargs: Any) -> Any:
        try:
            await self.publish_update('progress_update',
                                      {'progress_log':
                                       f"Tool '{self.current_tool}' finished"})
            # If a tool is ending, reset the current tool so that we may
            # send response chunks if the main agent is generating
            self.current_tool = None
            self.tool_depth -= 1
        except Exception as e:
            logger.error(f"Error in on_tool_end: {e}")

    async def on_agent_finish(self, finish: Any, *, run_id: UUID,
                              parent_run_id: Optional[UUID] = None,
                              **kwargs: Any) -> Any:
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent finished"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent finished"})
        except Exception as e:
            logger.error(f"Error in on_chat_model_start: {e}")


async def run_ai_task(task_id, user_id, thread_id, agent_id):
    """
    Async version of the AI task function to run
    in background thread via asyncio.run.
    Uses custom callbacks for progress synthesis and streaming.
    """
    # Import inside to avoid circular issues
    from django.contrib.auth.models import User

    # Initialize variables to avoid UnboundLocalError in exception handler
    task = None
    handler = None
    channel_layer = get_channel_layer()  # Get layer for publishing
    llm = None

    try:
        # Get all required objects with proper error handling (async-safe)
        task = await sync_to_async(Task.objects.get,
                                   thread_sensitive=False)(id=task_id)
        user = await sync_to_async(User.objects.get,
                                   thread_sensitive=False)(id=user_id)
        thread = await sync_to_async(Thread.objects.get,
                                     thread_sensitive=False)(id=thread_id)
        agent_obj = await sync_to_async(Agent.objects.get,
                                        thread_sensitive=False)(id=agent_id) if agent_id else None

        # Set task to running and log start
        task.status = TaskStatus.RUNNING
        task.progress_logs = [{"step": "Starting AI processing",
                              "timestamp":
                               str(dt.datetime.now(dt.timezone.utc))}]
        await sync_to_async(task.save, thread_sensitive=False)()

        # Get message history (wrap sync field accesses)
        messages = await sync_to_async(thread.get_messages,
                                       thread_sensitive=False)()

        # Sync function to build history and extract last_message
        def build_msg_history_sync(messages):
            msg_history = [[m.actor, m.text] for m in messages]
            if msg_history:
                msg_history.pop()  # Exclude last user message for consistency
            last_message = messages.last().text if messages else ''
            return msg_history, last_message

        msg_history, last_message = await sync_to_async(build_msg_history_sync,
                                                        thread_sensitive=False)(messages)

        # Create custom handler and LLMAgent with callbacks
        handler = TaskProgressHandler(task_id, channel_layer)
        llm = await LLMAgent.create(user, thread,
                                    agent=agent_obj,
                                    callbacks=[handler])

        try:
            # Run LLMAgent (now async)
            final_output = await llm.ainvoke(last_message)

            # Log completion and save result
            task.progress_logs.append({"step": "AI processing completed",
                                      "timestamp":
                                       str(dt.datetime.now(dt.timezone.utc))})
            task.result = final_output

            # Add final message to thread
            await sync_to_async(thread.add_message,
                                thread_sensitive=False)(final_output,
                                                        actor=Actor.AGENT)

            # Update thread subject if needed (now async) - wrap field access
            def check_and_update_subject_sync(thread):
                if thread.subject.startswith("thread n°"):
                    return True
                return False

            needs_title_update = await sync_to_async(check_and_update_subject_sync, thread_sensitive=False)(thread)
            if needs_title_update:
                short_title = await llm.ainvoke(
                    "Give a short title for this conversation (1–3 words maximum).\
                     Use the same language as the conversation.\
                     Answer by giving only the title, nothing else.",
                    silent_mode=True
                )
                thread.subject = short_title.strip()
                await sync_to_async(thread.save, thread_sensitive=False)()

            # Send a final progress update for task
            # This will be used for updating the
            # thread name and closing the socket
            await handler.publish_update('task_complete',
                                         {'result': final_output})
            task.status = TaskStatus.COMPLETED
            await sync_to_async(task.save, thread_sensitive=False)()

        finally:
            # Ensure cleanup even on error during invoke
            await llm.cleanup()

    except Exception as e:
        # Handle failure - safely handle case where task might be None
        logger.error(f"Task {task_id} failed: {e}")

        if task is not None:
            try:
                task.status = TaskStatus.FAILED
                task.result = f"Error: {str(e)}"
                if hasattr(task, 'progress_logs') and task.progress_logs:
                    task.progress_logs.append({"step": f"Error occurred: {str(e)}",
                                              "timestamp":
                                               str(dt.datetime.now(dt.timezone.utc))})
                else:
                    task.progress_logs = [{"step": f"Error occurred: {str(e)}",
                                          "timestamp":
                                           str(dt.datetime.now(dt.timezone.utc))}]
                await sync_to_async(task.save, thread_sensitive=False)()
            except Exception as save_error:
                logger.error(f"Failed to save task {task_id} error state: {save_error}")

        # Only try to publish update if handler was created
        if handler is not None:
            try:
                await handler.publish_update('task_complete', {'error': str(e)})
            except Exception as publish_error:
                logger.error(f"Failed to publish task {task_id} error update: {publish_error}")

        # Cleanup if llm was created
        if llm is not None:
            try:
                await llm.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup after error in task {task_id}: {cleanup_error}")
