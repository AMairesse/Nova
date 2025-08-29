# nova/tasks.py
import datetime as dt
from uuid import UUID
from typing import Any, Dict, List, Optional
from markdown import markdown
import bleach
from channels.layers import get_channel_layer
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from asgiref.sync import sync_to_async
from nova.models.models import TaskStatus
from nova.models.Message import Actor
from nova.llm.checkpoints import get_checkpointer
from nova.llm.llm_agent import LLMAgent
import logging

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
                raw_html = markdown(full_response,
                                    extensions=MARKDOWN_EXTENSIONS,
                                    extension_configs=MARKDOWN_EXTENSION_CONFIGS)
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


async def retrieve_context_consumption(agent_config, agent):
    # NEW: Compute context size after response (use async getter)
    config = agent.config
    checkpointer = await get_checkpointer()
    checkpoint_tuple = await checkpointer.aget_tuple(config)

    if checkpoint_tuple:
        # Extract the state dict from the checkpoint;
        # memory is often in channels like 'messages'
        state = checkpoint_tuple.checkpoint
        memory = state.get('channel_values', {}).get('messages', [])

    # Try to get the info from last response
    real_tokens = None
    approx_tokens = None
    if len(memory) > 0:
        last_response = memory[-1]
        # Get usage_metadata if available
        usage_metadata = last_response.usage_metadata
        if usage_metadata:
            real_tokens = usage_metadata.get('total_tokens', None)

    # If the info wasn't available, compute it from the context
    if real_tokens is None:
        byte_size = 0
        for m in memory:
            if isinstance(m, BaseMessage):
                byte_size += len(m.content.encode("utf-8", "ignore"))

        approx_tokens = byte_size // 4 + 1

    # Get max from provider's config
    max_context = await sync_to_async(lambda: agent_config.llm_provider.max_context_tokens, thread_sensitive=False)()

    return real_tokens, approx_tokens, max_context


async def run_ai_task(task, user, thread, agent_config, new_message):
    """
    Async version of the AI task function to run
    in background thread via asyncio.run.
    Uses custom callbacks for progress synthesis and streaming.
    """
    # Initialize variables to avoid UnboundLocalError in exception handler
    handler = None
    channel_layer = get_channel_layer()  # Get layer for publishing
    llm = None

    try:
        # Set task to running and log start
        task.status = TaskStatus.RUNNING
        task.progress_logs = [{"step": "Starting AI processing",
                              "timestamp":
                               str(dt.datetime.now(dt.timezone.utc))}]
        await sync_to_async(task.save, thread_sensitive=False)()

        # Create custom handler and LLMAgent with callbacks
        handler = TaskProgressHandler(task.id, channel_layer)
        llm = await LLMAgent.create(user, thread,
                                    agent_config,
                                    callbacks=[handler])

        try:
            # Run LLMAgent
            final_output = await llm.ainvoke(new_message)

            # Log completion and save result
            task.progress_logs.append({"step": "AI processing completed",
                                      "timestamp":
                                       str(dt.datetime.now(dt.timezone.utc))})
            task.result = final_output

            # Add final message to thread
            message = await sync_to_async(thread.add_message,
                                          thread_sensitive=False)(final_output,
                                                                  actor=Actor.AGENT)

            # Approximate token consumption relative to max context for this agent
            real_tokens, approx_tokens, \
                max_context = await retrieve_context_consumption(agent_config,
                                                                 llm)

            # Publish context consumption
            await handler.publish_update('context_consumption',
                                         {'real_tokens': real_tokens,
                                          'approx_tokens': approx_tokens,
                                          'max_context': max_context})

            # Store in message internal_data
            message.internal_data.update({
                'real_tokens': real_tokens,
                'approx_tokens': approx_tokens,
                'max_context': max_context
            })
            await sync_to_async(message.save, thread_sensitive=False)()

            # Update thread subject if needed - wrap field access
            def check_and_update_subject_sync(thread):
                if thread.subject.startswith("thread n°"):
                    return True
                return False

            needs_title_update = await sync_to_async(check_and_update_subject_sync,
                                                     thread_sensitive=False)(thread)
            if needs_title_update:
                short_title = await llm.ainvoke(
                    "Give a short title for this conversation (1–3 words).\
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
        logger.error(f"Task {task.id} failed: {e}")

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
                logger.error(f"Failed to save task {task.id} error state: {save_error}")

        # Only try to publish update if handler was created
        if handler is not None:
            try:
                await handler.publish_update('task_complete', {'error': str(e)})
            except Exception as publish_error:
                logger.error(f"Failed to publish task {task.id} error update: {publish_error}")

        # Cleanup if llm was created
        if llm is not None:
            try:
                await llm.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup after error in task {task.id}: {cleanup_error}")
