# nova/tasks.py
import datetime as dt
from uuid import UUID
from typing import Any, Dict, List, Optional
from celery import shared_task
import asyncio
from channels.layers import get_channel_layer
from django.contrib.auth.models import User
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from asgiref.sync import sync_to_async
from nova.models.models import Agent, Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.Message import Message
from nova.models.Message import Actor
from nova.llm.checkpoints import get_checkpointer
from nova.llm.llm_agent import LLMAgent
from nova.utils import markdown_to_html
import logging
import functools
from enum import Enum


class TaskErrorCategory(Enum):
    AGENT_FAILURE = "agent_failure"
    TOOL_FAILURE = "tool_failure"
    SYSTEM_ERROR = "system_error"
    VALIDATION_ERROR = "validation_error"
    NETWORK_ERROR = "network_error"


def task_error_handler(category: TaskErrorCategory = TaskErrorCategory.SYSTEM_ERROR):
    """
    Decorator for handling task errors with proper logging and state management.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            task = None
            handler = None
            llm = None

            # Extract task from arguments
            for arg in args:
                if hasattr(arg, 'status'):
                    task = arg
                    break

            try:
                return await func(*args, **kwargs)
            except Exception as e:
                error_msg = f"{category.value}: {str(e)}"
                logger.error(f"Task error in {func.__name__}: {error_msg}")

                if task:
                    await _handle_task_error(task, error_msg, category)

                if handler:
                    try:
                        await handler.publish_update('task_error',
                                                     {'error': error_msg, 'category': category.value})
                    except Exception as publish_error:
                        logger.error(f"Failed to publish error update: {publish_error}")

                if llm:
                    try:
                        await llm.cleanup()
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup LLM: {cleanup_error}")

                raise
        return wrapper
    return decorator


async def _handle_task_error(task, error_msg, category):
    """Handle task error with proper state management and logging."""
    try:
        task.status = TaskStatus.FAILED
        task.result = error_msg

        # Enhanced progress logging with error details
        error_log = {
            "step": f"Error: {error_msg}",
            "category": category.value,
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "error"
        }

        if hasattr(task, 'progress_logs') and task.progress_logs:
            task.progress_logs.append(error_log)
        else:
            task.progress_logs = [error_log]

        await sync_to_async(task.save, thread_sensitive=False)()
    except Exception as save_error:
        logger.error(f"Failed to save task error state: {save_error}")


logger = logging.getLogger(__name__)


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

    async def on_llm_start(self, serialized: Dict[str, Any],
                           prompts: List[str], **kwargs: Any):
        """Unified LLM start handler for both chat and completion models."""
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent started"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log': "Sub-agent started"})
        except Exception as e:
            logger.error(f"Error in on_llm_start: {e}")

    async def on_llm_new_token(self, token: str, *, run_id: UUID,
                               parent_run_id: Optional[UUID] = None,
                               **kwargs: Any) -> Any:
        try:
            # Send only chunks from the root run
            if self.tool_depth == 0:
                self.final_chunks.append(token)
                full_response = ''.join(self.final_chunks)
                clean_html = markdown_to_html(full_response)
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


class TaskExecutor:
    """Base class for task execution of a agent call.

    Args:
        task: The task object.
        user: The user object.
        thread: The thread object.
        agent_config: The agent configuration.
        prompt: The prompt to be used for the agent.

    Recommanded methods to super-class:
        _create_prompt() : to dynamically create the prompt instead of providing it on initialization
        _process_result(result) : to process the result of the agent and do any necessary updates

    """
    def __init__(self, task, user, thread, agent_config, prompt):
        self.task = task
        self.user = user
        self.thread = thread
        self.agent_config = agent_config
        self.prompt = prompt
        self.handler = None
        self.llm = None
        self.channel_layer = get_channel_layer()

    async def execute(self):
        """Main execution method with comprehensive error handling."""
        try:
            await self._initialize_task()
            await self._create_llm_agent()
            self.prompt = await self._create_prompt()
            result = await self._run_agent()
            await self._process_result(result)
            await self._finalize_task()
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

    async def _initialize_task(self):
        """Initialize task state and logging."""
        self.task.status = TaskStatus.RUNNING
        self.task.progress_logs = [{
            "step": "Initializing AI task",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        }]
        await sync_to_async(self.task.save, thread_sensitive=False)()

        self.handler = TaskProgressHandler(self.task.id, self.channel_layer)

    async def _create_llm_agent(self):
        """Create and configure the LLM agent."""
        self.task.progress_logs.append({
            "step": "Creating LLM agent",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        })
        await sync_to_async(self.task.save, thread_sensitive=False)()

        self.llm = await LLMAgent.create(
            self.user, self.thread, self.agent_config,
            callbacks=[self.handler]
        )

    async def _create_prompt(self):
        return self.prompt

    async def _run_agent(self):
        """Execute the LLM agent and return result."""
        self.task.progress_logs.append({
            "step": "Running AI agent",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        })
        await sync_to_async(self.task.save, thread_sensitive=False)()

        return await self.llm.ainvoke(self.prompt)

    async def _finalize_task(self):
        """Finalize the task as completed."""
        self.task.progress_logs.append({
            "step": "Task completed successfully",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "success"
        })

        await self.handler.publish_update('task_complete', {
            'result': self.task.result,
            'thread_id': self.thread.id,
            'thread_subject': self.thread.subject
        })

        self.task.status = TaskStatus.COMPLETED
        await sync_to_async(self.task.save, thread_sensitive=False)()

    async def _handle_execution_error(self, error):
        """Handle execution errors with proper categorization."""
        error_category = self._categorize_error(error)

        error_msg = f"{error_category.value}: {str(error)}"
        logger.error(f"Task {self.task.id} failed: {error_msg}")

        # Update task state
        self.task.status = TaskStatus.FAILED
        self.task.result = error_msg

        # Enhanced error logging
        error_log = {
            "step": f"Execution failed: {str(error)}",
            "category": error_category.value,
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "error",
            "error_details": {
                "type": type(error).__name__,
                "message": str(error)
            }
        }
        self.task.progress_logs.append(error_log)
        await sync_to_async(self.task.save, thread_sensitive=False)()

        # Publish error update
        if self.handler:
            try:
                await self.handler.publish_update('task_error', {
                    'error': error_msg,
                    'category': error_category.value
                })
            except Exception as publish_error:
                logger.error(f"Failed to publish error update: {publish_error}")

    def _categorize_error(self, error):
        """Categorize the error for better handling."""
        error_str = str(error).lower()
        error_type = type(error).__name__

        if "tool" in error_str or "Tool" in error_type:
            return TaskErrorCategory.TOOL_FAILURE
        elif "agent" in error_str or "Agent" in error_type:
            return TaskErrorCategory.AGENT_FAILURE
        elif "network" in error_str or "connection" in error_str:
            return TaskErrorCategory.NETWORK_ERROR
        elif "validation" in error_str:
            return TaskErrorCategory.VALIDATION_ERROR
        else:
            return TaskErrorCategory.SYSTEM_ERROR

    async def _cleanup(self):
        """Ensure proper cleanup of resources."""
        if self.llm:
            try:
                await self.llm.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup LLM: {cleanup_error}")

    async def _process_result(self, result):
        """Process the agent result and update related data."""
        self.task.progress_logs.append({
            "step": "Processing agent result",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        })
        await sync_to_async(self.task.save, thread_sensitive=False)()

        # Save result
        self.task.result = result


class AgentTaskExecutor (TaskExecutor):
    """
    Encapsulates the execution of AI tasks with proper error handling,
    progress tracking, and state management.
    """

    async def _process_result(self, result):
        super()._process_result(result)

        # Add message to thread
        message = await sync_to_async(
            self.thread.add_message, thread_sensitive=False
        )(result, actor=Actor.AGENT)

        # Calculate and store context consumption
        real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(
            self.agent_config, self.llm
        )

        # Update progress logs with consumption info
        self.task.progress_logs.append({
            "step": f"Context consumption: {real_tokens or approx_tokens} tokens",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info",
            "context_info": {
                "real_tokens": real_tokens,
                "approx_tokens": approx_tokens,
                "max_context": max_context
            }
        })

        # Publish context consumption
        await self.handler.publish_update('context_consumption', {
            'real_tokens': real_tokens,
            'approx_tokens': approx_tokens,
            'max_context': max_context
        })

        # Store in message
        message.internal_data.update({
            'real_tokens': real_tokens,
            'approx_tokens': approx_tokens,
            'max_context': max_context
        })
        await sync_to_async(message.save, thread_sensitive=False)()

        # Update thread subject if needed
        await self._update_thread_subject()

    async def _update_thread_subject(self):
        """Update thread subject if it's a default title."""
        if self.thread.subject.startswith("thread n°"):
            title = await self.llm.ainvoke(
                "Give a short title for this conversation (1–3 words). "
                "Use the same language as the conversation. "
                "Answer by giving only the title, nothing else.",
                silent_mode=True
            )
            self.thread.subject = title.strip()
            await sync_to_async(self.thread.save, thread_sensitive=False)()


class ContextConsumptionTracker:
    """Utility class for tracking and calculating context consumption."""

    @staticmethod
    async def calculate(agent_config, agent):
        """
        Calculate context consumption from agent checkpoint.
        Returns (real_tokens, approx_tokens, max_context)
        """
        config = agent.config
        checkpointer = await get_checkpointer()
        checkpoint_tuple = await checkpointer.aget_tuple(config)

        real_tokens = None
        approx_tokens = None

        if checkpoint_tuple:
            state = checkpoint_tuple.checkpoint
            memory = state.get('channel_values', {}).get('messages', [])

            # Try to get real token count from last response
            if memory:
                last_response = memory[-1]
                usage_metadata = getattr(last_response, 'usage_metadata', None)
                if usage_metadata:
                    real_tokens = usage_metadata.get('total_tokens')

            # Fallback to approximation
            if real_tokens is None:
                approx_tokens = ContextConsumptionTracker._approximate_tokens(memory)

        # Get max context from provider
        max_context = await sync_to_async(
            lambda: agent_config.llm_provider.max_context_tokens,
            thread_sensitive=False
        )()

        return real_tokens, approx_tokens, max_context

    @staticmethod
    def _approximate_tokens(memory):
        """Approximate token count from message content."""
        total_bytes = 0

        for msg in memory:
            if not isinstance(msg, BaseMessage):
                continue

            content = msg.content
            if isinstance(content, str):
                # Handle string content
                total_bytes += len(content.encode("utf-8", "ignore"))
            elif isinstance(content, list):
                # Handle list content - iterate through each item
                for item in content:
                    if isinstance(item, str):
                        total_bytes += len(item.encode("utf-8", "ignore"))
                    else:
                        # Convert non-string items to string representation
                        total_bytes += len(str(item).encode("utf-8", "ignore"))
            else:
                # Handle other content types by converting to string
                total_bytes += len(str(content).encode("utf-8", "ignore"))

        return total_bytes // 4 + 1


class CompactTaskExecutor (TaskExecutor):
    """
    Encapsulates the execution of a compact task
    """

    async def _create_prompt(self):
        # Emit progress update for analysis phase
        await self.handler.publish_update('progress_update',
                                          {'progress_log': "Analyzing conversation..."})

        # Retrieve context consumption
        real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(
            self.agent_config, self.llm
        )

        target_tokens = int(real_tokens or approx_tokens * 0.3)
        target_words = int(target_tokens / 0.75)

        # Prompt for summary (partie sync, inchangée)
        prompt = f"""Summarize the conversation to a maximum of {target_words} words,
                     Capture key points, user intent, and outcomes without adding new information.
                     Default to the conversation's language and reply in Markdown."""
        return prompt

    async def _run_agent(self):
        """Execute the LLM agent and return result."""
        self.task.progress_logs.append({
            "step": "Generating summary...",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        })
        await sync_to_async(self.task.save, thread_sensitive=False)()

        await self.handler.publish_update('progress_update',
                                          {'progress_log': "Generating summary..."})

        return await self.llm.ainvoke(self.prompt)

    async def _process_result(self, result):
        await super()._process_result(result)

        # Emit progress update for checkpoint update
        await self.handler.publish_update('progress_update',
                                          {'progress_log': "Updating context..."})

        config = self.llm.config

        # Remove old checkpoints
        thread_id = config['configurable']['thread_id']
        # thread_id = config['metadata']['thread_id']
        checkpointer = await get_checkpointer()
        await checkpointer.adelete_thread(thread_id)

        # Inject the summary
        from langchain_core.messages import AIMessage
        dummy_input = {"messages": [AIMessage(content=result, additional_kwargs={'summary': True})]} 

        graph = self.llm.langchain_agent
        await graph.ainvoke(dummy_input, config=config)

        # Add system message with summary details
        system_message_text = "ℹ️ Conversation compacted"
        system_message = await sync_to_async(self.thread.add_message, thread_sensitive=False)(
            system_message_text, actor=Actor.SYSTEM
        )
        system_message.internal_data = {
            'type': 'compact_complete',
            'summary': result
        }
        await sync_to_async(system_message.save, thread_sensitive=False)()
        
        # Process markdown to HTML server-side before sending the message
        system_message.internal_data['summary'] = markdown_to_html(system_message.internal_data['summary'])

        # Broadcast the new message to all connected WebSocket clients for real-time UI updates
        await self.handler.publish_update('new_message', {
            'message': {
                'id': system_message.id,
                'text': system_message.text,
                'actor': system_message.actor,
                'internal_data': system_message.internal_data,
                'created_at': system_message.created_at.isoformat() if hasattr(system_message.created_at, 'isoformat')
                else str(system_message.created_at)
            }
        })

    async def _finalize_task(self):
        """Finalize the task as completed."""
        self.task.progress_logs.append({
            "step": "Summary complete, context updated",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "success"
        })

        await self.handler.publish_update('task_complete', {
            'result': self.task.result,
            'thread_id': self.thread.id,
            'thread_subject': self.thread.subject
        })

        self.task.status = TaskStatus.COMPLETED
        await sync_to_async(self.task.save, thread_sensitive=False)()


@shared_task(bind=True, name="compact_conversation")
def compact_conversation_celery(self, task_pk, user_pk, thread_pk, agent_pk):
    """
    Celery task to summarize conversation and update checkpoint.
    """
    try:
        # Fetch objects
        task = Task.objects.select_related('user', 'thread').get(pk=task_pk)
        user = User.objects.get(pk=user_pk)
        thread = Thread.objects.select_related('user').get(pk=thread_pk)
        agent_config = Agent.objects.select_related('llm_provider').get(pk=agent_pk) if agent_pk else None

        # Call the agent
        executor = CompactTaskExecutor(task, user, thread, agent_config, "")
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Celery task {task_pk} failed: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="run_ai_task")
def run_ai_task_celery(self, task_pk, user_pk, thread_pk, agent_pk, message_pk):
    """
    Optimized Celery task with batched database queries and AgentTaskExecutor.
    """
    try:
        # Optimized database queries with select_related
        task = Task.objects.select_related('user', 'thread').get(pk=task_pk)
        user = User.objects.get(pk=user_pk)
        thread = Thread.objects.select_related('user').get(pk=thread_pk)

        agent_config = None
        if agent_pk:
            agent_config = Agent.objects.select_related('llm_provider').get(pk=agent_pk)

        message = Message.objects.select_related('thread', 'user').get(pk=message_pk)

        # Use the AgentTaskExecutor for cleaner execution
        executor = AgentTaskExecutor(task, user, thread, agent_config, message)
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Celery task {task_pk} failed: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)
