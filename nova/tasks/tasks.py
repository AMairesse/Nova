# nova/tasks/tasks.py
import asyncio
import datetime as dt
import logging
from asgiref.sync import sync_to_async
from celery import shared_task

from django.contrib.auth.models import User
from django.utils import timezone
from langchain_core.messages import BaseMessage

from nova.llm.checkpoints import get_checkpointer
from nova.models.AgentConfig import AgentConfig
from nova.models.Interaction import Interaction
from nova.models.Message import Message
from nova.models.Message import Actor
from nova.models.ScheduledTask import ScheduledTask
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tasks.TaskExecutor import TaskExecutor

logger = logging.getLogger(__name__)


class AgentTaskExecutor (TaskExecutor):
    """
    Encapsulates the execution of AI tasks with proper error handling,
    progress tracking, and state management.
    """

    async def _process_result(self, result):
        await super()._process_result(result)

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
        await self.handler.on_context_consumption(real_tokens, approx_tokens, max_context)

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
        try:
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
        finally:
            await checkpointer.conn.close()

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


async def delete_checkpoints(ckp_id):
    checkpointer = await get_checkpointer()
    try:
        await checkpointer.adelete_thread(ckp_id)
    finally:
        await checkpointer.conn.close()


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
            agent_config = AgentConfig.objects.select_related('llm_provider').get(pk=agent_pk)

        message = Message.objects.select_related('thread', 'user').get(pk=message_pk)
        prompt_text = message.text or ""

        # Use the AgentTaskExecutor for cleaner execution
        executor = AgentTaskExecutor(task, user, thread, agent_config, prompt_text)
        asyncio.run(executor.execute_or_resume())

    except Exception as e:
        logger.error(f"Celery task {task_pk} failed: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="resume_ai_task")
def resume_ai_task_celery(self, interaction_pk: int):
    """
    Resume an agent execution after user input.
    Uses the same thread/checkpoint and streams via the same WS group (task_id).
    """
    try:
        interaction = Interaction.objects.select_related('task', 'thread', 'agent_config').get(pk=interaction_pk)
        task = interaction.task
        thread = interaction.thread
        user = task.user
        agent_config = interaction.agent_config

        # Build the interruption_response
        interruption_response = {
            'action': "user_response",
            'user_response': interaction.answer,
            'interaction_id': interaction.id,
            'interaction_status': interaction.status,
        }

        # Run the resume executor
        executor = AgentTaskExecutor(task, user, thread, agent_config, interaction)
        asyncio.run(executor.execute_or_resume(interruption_response))

    except Exception as e:
        logger.error(f"Celery resume_ai_task for interaction {interaction_pk} failed: {e}")
        raise self.retry(countdown=30, exc=e)


@shared_task(bind=True, name="summarize_thread_task")
def summarize_thread_task(self, thread_id, user_id, agent_config_id, task_id):
    """
    Celery task to manually summarize a thread.
    """
    try:
        from nova.tasks.TaskProgressHandler import TaskProgressHandler
        from nova.llm.agent_middleware import AgentContext

        # Get objects
        thread = Thread.objects.get(id=thread_id, user_id=user_id)
        user = User.objects.get(id=user_id)
        agent_config = AgentConfig.objects.get(id=agent_config_id, user=user)
        task = Task.objects.get(id=task_id)

        # Create progress handler for WebSocket updates
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        progress_handler = TaskProgressHandler(task.id, channel_layer)

        # Update task status
        task.status = TaskStatus.RUNNING
        task.save()

        try:
            # Create the full agent (same as automatic summarization)
            from nova.llm.llm_agent import LLMAgent
            llm_agent = asyncio.run(LLMAgent.create(user, thread, agent_config))

            # Create context for middleware
            context = AgentContext(
                agent_config=agent_config,
                user=user,
                thread=thread,
                progress_handler=progress_handler
            )

            # Find the summarization middleware and call manual_summarize
            middleware = None
            for mw in llm_agent.middleware:
                if hasattr(mw, 'manual_summarize'):
                    middleware = mw
                    break

            if not middleware:
                raise ValueError("SummarizationMiddleware not found on agent")

            # Perform manual summarization using the same logic as automatic
            result = asyncio.run(middleware.manual_summarize(context))

            if result["status"] == "success":
                logger.info(f"Thread {thread_id} summarization completed successfully")
                # Mark task as completed
                task.status = TaskStatus.COMPLETED
                task.save()
            else:
                raise ValueError(f"Summarization failed: {result['message']}")

        except Exception as e:
            # Mark task as failed
            task.status = TaskStatus.FAILED
            task.save()
            asyncio.run(progress_handler.on_progress(f"Summarization failed: {str(e)}"))
            raise

    except Exception as e:
        logger.error(f"Summarization task failed for thread {thread_id}: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="run_scheduled_agent_task")
def run_scheduled_agent_task(self, scheduled_task_id):
    """
    Celery task to execute a scheduled agent task.
    """
    try:
        # Get the scheduled task
        scheduled_task = ScheduledTask.objects.get(id=scheduled_task_id)

        if not scheduled_task.is_active:
            logger.info(f"Scheduled task {scheduled_task.name} is not active, skipping.")
            return

        # Create a new thread for this execution
        thread = Thread.objects.create(
            user=scheduled_task.user,
            subject=scheduled_task.name
        )

        # Add the prompt as a user message if thread will be kept
        if scheduled_task.keep_thread:
            thread.add_message(scheduled_task.prompt, Actor.USER, "standard")

        # Create a Task instance for consistency with other agent executions
        task = Task.objects.create(
            user=scheduled_task.user,
            thread=thread,
            agent_config=scheduled_task.agent,
            status=TaskStatus.RUNNING
        )

        # Use AgentTaskExecutor for consistent execution
        executor = AgentTaskExecutor(task, scheduled_task.user, thread, scheduled_task.agent, scheduled_task.prompt)
        asyncio.run(executor.execute_or_resume())

        # Mark task as completed
        task.status = TaskStatus.COMPLETED
        task.save()

        # Update last run time
        scheduled_task.last_run_at = timezone.now()
        scheduled_task.last_error = None  # Clear any previous error
        scheduled_task.save()

        # Optionally delete the thread
        if not scheduled_task.keep_thread:
            thread.delete()

        logger.info(f"Scheduled task {scheduled_task.name} executed successfully.")

    except Exception as e:
        logger.error(f"Error executing scheduled task {scheduled_task_id}: {e}", exc_info=True)

        # Update the scheduled task with the error
        try:
            scheduled_task = ScheduledTask.objects.get(id=scheduled_task_id)
            scheduled_task.last_error = str(e)
            scheduled_task.last_run_at = timezone.now()
            scheduled_task.save()
        except Exception as inner_e:
            logger.error(f"Failed to update scheduled task with error: {inner_e}")
