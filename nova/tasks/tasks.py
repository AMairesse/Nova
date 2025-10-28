# nova/tasks/tasks.py
import asyncio
import datetime as dt
import logging
import functools
from asgiref.sync import sync_to_async
from celery import shared_task
from channels.layers import get_channel_layer

from django.contrib.auth.models import User
from langchain_core.messages import BaseMessage

from nova.llm.checkpoints import get_checkpointer
from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Message
from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tasks.TaskExecutor import TaskExecutor, TaskErrorCategory
from nova.utils import markdown_to_html

logger = logging.getLogger(__name__)


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


class ResumeTaskExecutor(AgentTaskExecutor):
    """
    Executor that resumes an interrupted agent run after user input.
    It builds a concise resume prompt from the Interaction (question + answer)
    and continues on the same thread/checkpoint.
    """
    def __init__(self, task, user, thread, agent_config, interaction: Interaction):
        super().__init__(task, user, thread, agent_config, prompt="")
        self.interaction = interaction

    async def _create_prompt(self):
        # Build a concise resume instruction
        import json as _json

        q = self.interaction.question or ""
        ans = self.interaction.answer
        if isinstance(ans, (dict, list)):
            ans_text = _json.dumps(ans, ensure_ascii=False)
        else:
            ans_text = str(ans)

        # Keep it short and directive to avoid re-asking
        resume_prompt = (
            "Resume the previous task exactly where you left off.\n"
            f"You had asked the user this clarification question: \"{q}\"\n"
            f"The user's answer is: {ans_text}\n"
            "Do not ask the same question again. Use this answer to proceed and complete the task."
        )
        return resume_prompt


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
                     Use the same language as the conversation's language and reply in Markdown."""
        return prompt

    async def _process_result(self, result):
        await super()._process_result(result)

        # Emit progress update for checkpoint update
        await self.handler.publish_update('progress_update',
                                          {'progress_log': "Updating context"})

        config = self.llm.config

        # Remove old checkpoints
        thread_id = config['configurable']['thread_id']
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


async def delete_checkpoints(ckp_id):
    checkpointer = await get_checkpointer()
    await checkpointer.adelete_thread(ckp_id)


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
        agent_config = AgentConfig.objects.select_related('llm_provider').get(pk=agent_pk) if agent_pk else None

        # Call the agent
        executor = CompactTaskExecutor(task, user, thread, agent_config, "")
        asyncio.run(executor.execute())

        # Find and delete sub-agents' checkpoints
        other_agents_ckp = CheckpointLink.objects.filter(thread=thread).exclude(agent=agent_config)
        for ckp in other_agents_ckp:
            # Get the checkpoint_id
            checkpoint_id = ckp.checkpoint_id
            # Delete the checkpoint
            logger.info(f"Deleting checkpoint {checkpoint_id}")
            asyncio.run(delete_checkpoints(checkpoint_id))
            ckp.delete()

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
            agent_config = AgentConfig.objects.select_related('llm_provider').get(pk=agent_pk)

        message = Message.objects.select_related('thread', 'user').get(pk=message_pk)
        prompt_text = message.text or ""

        # Use the AgentTaskExecutor for cleaner execution
        executor = AgentTaskExecutor(task, user, thread, agent_config, prompt_text)
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Celery task {task_pk} failed: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="resume_ai_task")
def resume_ai_task_celery(self, interaction_pk: int):
    """
    Resume an agent execution after user input by running the ResumeTaskExecutor.
    Uses the same thread/checkpoint, streams via the same WS group (task_id).
    """
    try:
        interaction = Interaction.objects.select_related('task', 'thread', 'agent').get(pk=interaction_pk)
        task = interaction.task
        thread = interaction.thread
        user = task.user
        agent_config = interaction.agent  # set by ask_user tool; required

        if interaction.status != InteractionStatus.ANSWERED:
            # Nothing to do yet
            return

        if agent_config is None:
            raise ValueError("Missing agent configuration on Interaction; cannot resume.")

        # Mark task as RUNNING and append a progress log entry
        if not task.progress_logs:
            task.progress_logs = []
        task.progress_logs.append({
            "step": "Resuming after user input",
            "timestamp": str(dt.datetime.now(dt.timezone.utc)),
            "severity": "info"
        })
        task.status = TaskStatus.RUNNING
        task.save(update_fields=['status', 'progress_logs'])

        # Notify UI that we are resuming (helps re-enable input proactively)
        channel_layer = get_channel_layer()

        async def notify():
            await channel_layer.group_send(
                f"task_{task.id}",
                {'type': 'task_update', 'message': {
                    'type': 'interaction_update',
                    'interaction_id': interaction.id,
                    'status': 'RESUMING'
                }}
            )
            await channel_layer.group_send(
                f"task_{task.id}",
                {'type': 'task_update', 'message': {
                    'type': 'progress_update',
                    'progress_log': "Resuming after user input"
                }}
            )

        asyncio.run(notify())

        # Run the resume executor
        executor = ResumeTaskExecutor(task, user, thread, agent_config, interaction)
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Celery resume_ai_task for interaction {interaction_pk} failed: {e}")
        raise self.retry(countdown=30, exc=e)
