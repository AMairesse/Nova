# nova/tasks/tasks.py
import asyncio
import datetime as dt
import logging
from asgiref.sync import sync_to_async, async_to_sync
from celery import current_app
from celery import shared_task
from channels.layers import get_channel_layer

from django.contrib.auth.models import User
from django.utils import timezone
from langchain_core.messages import BaseMessage, HumanMessage

from nova.llm.checkpoints import get_checkpointer
from nova.llm.llm_agent import LLMAgent, create_provider_llm
from nova.models.AgentConfig import AgentConfig
from nova.models.Interaction import Interaction
from nova.models.Message import Message
from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.email_polling import poll_new_unseen_email_headers
from nova.tasks.TaskExecutor import TaskExecutor
from nova.tasks.task_definition_runner import build_email_prompt_variables, execute_agent_task_definition
from nova.thread_titles import is_default_thread_subject, normalize_generated_thread_title
from nova.utils import strip_thinking_blocks

logger = logging.getLogger(__name__)

TRIGGER_TASK_MAX_RETRIES = 5
TRIGGER_TASK_RETRY_BASE_SECONDS = 30
TRIGGER_TASK_RETRY_MAX_SECONDS = 15 * 60
THREAD_TITLE_PROMPT = (
    "Generate a concise thread title (2-6 words) from this conversation excerpt. "
    "Use the same language as the conversation. "
    "Return title only, with no punctuation wrapper and no explanation."
)


def compute_trigger_retry_countdown(retries: int) -> int:
    """Compute exponential backoff countdown for trigger task retries."""
    retries = max(int(retries or 0), 0)
    raw_countdown = TRIGGER_TASK_RETRY_BASE_SECONDS * (2 ** retries)
    return min(raw_countdown, TRIGGER_TASK_RETRY_MAX_SECONDS)


def schedule_trigger_task_retry(task, error: Exception, *, task_definition_id: int, runner_name: str) -> bool:
    """Schedule a retry for trigger-driven tasks, or return False when exhausted."""
    retries = int(getattr(getattr(task, "request", None), "retries", 0) or 0)
    if retries >= TRIGGER_TASK_MAX_RETRIES:
        logger.error(
            "Task definition %s (%s) reached max retries (%s).",
            task_definition_id,
            runner_name,
            TRIGGER_TASK_MAX_RETRIES,
        )
        return False

    countdown = compute_trigger_retry_countdown(retries)
    logger.warning(
        "Retrying task definition %s (%s) in %ss (attempt %s/%s).",
        task_definition_id,
        runner_name,
        countdown,
        retries + 2,
        TRIGGER_TASK_MAX_RETRIES + 1,
    )
    raise task.retry(exc=error, countdown=countdown, max_retries=TRIGGER_TASK_MAX_RETRIES)


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

        # Trigger title generation asynchronously when the thread still has its default title.
        await self._enqueue_thread_title_generation()

    async def _enqueue_thread_title_generation(self):
        """Schedule thread title generation asynchronously for default subjects."""
        if not self.thread or not self.agent_config:
            return
        if not is_default_thread_subject(self.thread.subject):
            return
        await sync_to_async(generate_thread_title_task.delay, thread_sensitive=False)(
            thread_id=self.thread.id,
            user_id=self.user.id,
            agent_config_id=self.agent_config.id,
            source_task_id=self.task.id,
        )


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


def _build_langfuse_invoke_config(user, *, session_id: str):
    """Build Langfuse callbacks/config for direct LLM invocations outside LangGraph."""
    try:
        allow_langfuse, langfuse_public_key, langfuse_secret_key, langfuse_host = LLMAgent.fetch_user_params_sync(user)
    except Exception:
        logger.warning(
            "Could not load Langfuse user parameters for user %s. Continuing without tracing.",
            getattr(user, "id", "unknown"),
        )
        return {}, None
    if not (allow_langfuse and langfuse_public_key and langfuse_secret_key):
        return {}, None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        client = Langfuse(
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            host=langfuse_host,
        )
        if not client.auth_check():
            logger.warning(
                "Langfuse auth check failed for user %s during direct LLM call. Tracing will still be attempted.",
                getattr(user, "id", "unknown"),
            )

        invoke_config = {
            "callbacks": [CallbackHandler(public_key=langfuse_public_key)],
            "metadata": {
                "langfuse_session_id": session_id,
            },
        }
        return invoke_config, client
    except Exception:
        logger.exception(
            "Failed to initialize Langfuse callback for direct LLM call (user_id=%s).",
            getattr(user, "id", "unknown"),
        )
        return {}, None


def _build_thread_title_prompt(messages: list[Message]) -> str:
    """Build a short prompt from the first thread messages."""
    lines = []
    for msg in messages:
        role = "User" if msg.actor == Actor.USER else "Agent"
        text = (msg.text or "").strip()
        if not text:
            continue
        lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    return f"{THREAD_TITLE_PROMPT}\n\nConversation excerpt:\n{transcript}\n\nTitle:"


def _publish_thread_subject_update(source_task_id: int | None, thread_id: int, thread_subject: str) -> None:
    """Push a websocket update so the sidebar title updates live in the UI."""
    if not source_task_id:
        return
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    async_to_sync(channel_layer.group_send)(
        f"task_{source_task_id}",
        {
            "type": "task_update",
            "message": {
                "type": "thread_subject_updated",
                "thread_id": thread_id,
                "thread_subject": thread_subject,
            },
        },
    )


@shared_task(bind=True, name="generate_thread_title")
def generate_thread_title_task(
    self,
    *,
    thread_id: int,
    user_id: int,
    agent_config_id: int,
    source_task_id: int | None = None,
):
    """Generate a short thread title asynchronously without touching agent checkpoints."""
    try:
        thread = Thread.objects.select_related("user").get(id=thread_id, user_id=user_id)
        if not is_default_thread_subject(thread.subject):
            return {"status": "skipped", "reason": "subject_already_customized"}

        messages = list(
            Message.objects.filter(thread_id=thread_id, actor__in=[Actor.USER, Actor.AGENT])
            .order_by("created_at", "id")[:4]
        )
        if len(messages) < 2:
            return {"status": "skipped", "reason": "not_enough_messages"}

        agent_config = AgentConfig.objects.select_related("llm_provider").get(id=agent_config_id, user_id=user_id)
        llm = create_provider_llm(agent_config.llm_provider)

        invoke_config, langfuse_client = _build_langfuse_invoke_config(
            thread.user,
            session_id=f"thread_title_{thread_id}",
        )
        try:
            response = asyncio.run(
                llm.ainvoke(
                    [HumanMessage(content=_build_thread_title_prompt(messages))],
                    config=invoke_config or None,
                )
            )
        finally:
            if langfuse_client is not None:
                try:
                    langfuse_client.flush()
                    langfuse_client.shutdown()
                except Exception:
                    logger.warning("Failed to cleanup Langfuse client for thread title generation.")

        raw_title = strip_thinking_blocks(getattr(response, "content", None) or str(response))
        normalized_title = normalize_generated_thread_title(raw_title)
        if not normalized_title:
            return {"status": "skipped", "reason": "empty_generated_title"}

        # Do not overwrite if subject changed since task start (manual rename or parallel update).
        updated = Thread.objects.filter(
            id=thread_id,
            user_id=user_id,
            subject=thread.subject,
        ).update(subject=normalized_title)
        if not updated:
            return {"status": "skipped", "reason": "subject_changed_during_generation"}

        _publish_thread_subject_update(source_task_id, thread_id, normalized_title)
        return {"status": "ok", "thread_id": thread_id, "thread_subject": normalized_title}
    except Exception as e:
        logger.error("Thread title generation failed for thread %s: %s", thread_id, e, exc_info=True)
        raise


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
        executor = AgentTaskExecutor(task, user, thread, agent_config, prompt_text, source_message_id=message.id)
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
def summarize_thread_task(self, thread_id, user_id, agent_config_id, task_id,
                          include_sub_agents=False, sub_agent_ids=None):
    """
    Celery task to manually summarize a thread.
    Uses SummarizationTaskExecutor following the same pattern as AgentTaskExecutor.
    """
    try:
        # Get objects (sync database access)
        thread = Thread.objects.get(id=thread_id, user_id=user_id)
        user = User.objects.get(id=user_id)
        agent_config = AgentConfig.objects.get(id=agent_config_id, user=user)
        task = Task.objects.get(id=task_id)

        # Use the executor pattern (same as AgentTaskExecutor)
        executor = SummarizationTaskExecutor(task, user, thread, agent_config, include_sub_agents, sub_agent_ids or [])
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Summarization task failed for thread {thread_id}: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


class SummarizationTaskExecutor(TaskExecutor):
    """
    Executor for manual thread summarization.
    Follows the same pattern as AgentTaskExecutor for consistent behavior.
    """

    def __init__(self, task, user, thread, agent_config, include_sub_agents=False, sub_agent_ids=None):
        # Initialize with empty prompt - summarization doesn't need user input
        super().__init__(task, user, thread, agent_config, "", source_message_id=None)
        self.include_sub_agents = include_sub_agents
        self.sub_agent_ids = sub_agent_ids or []

    async def execute(self):
        """Main execution method for summarization."""
        try:
            await self._initialize_task()
            await self._create_llm_agent()
            await self._perform_summarization()
            await self._finalize_task()
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

    async def _perform_summarization(self):
        """Perform the summarization using the middleware."""
        if self.include_sub_agents:
            sub_agent_ids = self.sub_agent_ids

            # Summarize main agent first
            await self._summarize_single_agent(self.agent_config)

            # Then summarize each selected sub-agent
            for agent_id in sub_agent_ids:
                sub_agent_config = await sync_to_async(AgentConfig.objects.get, thread_sensitive=False)(
                    id=agent_id, user=self.user
                )
                await self._summarize_single_agent(sub_agent_config)
        else:
            await self._summarize_single_agent(self.agent_config)

        logger.info(f"Thread {self.thread.id} summarization completed successfully")

    async def _summarize_single_agent(self, agent_config):
        """Summarize a single agent."""
        from nova.llm.llm_agent import LLMAgent
        from nova.llm.agent_middleware import AgentContext

        # Create agent-specific LLMAgent instance
        agent = await LLMAgent.create(self.user, self.thread, agent_config)
        try:
            # Create context for middleware
            context = AgentContext(
                agent_config=agent_config,
                user=self.user,
                thread=self.thread,
                progress_handler=self.handler
            )

            # Find the summarization middleware
            middleware = None
            for mw in agent.middleware:
                if hasattr(mw, 'manual_summarize'):
                    middleware = mw
                    break

            if not middleware:
                raise ValueError(f"SummarizationMiddleware not found for agent {agent_config.name}")

            # Perform manual summarization
            result = await middleware.manual_summarize(context)

            if result["status"] != "success":
                raise ValueError(f"Summarization failed for {agent_config.name}: {result['message']}")

        finally:
            await agent.cleanup()


def _mark_task_definition_success(task_definition: TaskDefinition):
    task_definition.last_run_at = timezone.now()
    task_definition.last_error = None
    task_definition.save(update_fields=["last_run_at", "last_error", "updated_at"])


def _mark_task_definition_error(task_definition_id: int, error: Exception):
    try:
        task_definition = TaskDefinition.objects.get(id=task_definition_id)
        task_definition.last_error = str(error)
        task_definition.last_run_at = timezone.now()
        task_definition.save(update_fields=["last_error", "last_run_at", "updated_at"])
    except Exception as inner_e:
        logger.error("Failed to update task definition %s with error: %s", task_definition_id, inner_e)


@shared_task(bind=True, name="run_task_definition_cron")
def run_task_definition_cron(self, task_definition_id: int):
    """Run an agent task definition configured with a cron trigger."""
    try:
        task_definition = TaskDefinition.objects.select_related("user", "agent").get(id=task_definition_id)
        if not task_definition.is_active:
            logger.info("Task definition %s is inactive. Skipping.", task_definition.name)
            return {"status": "skipped", "reason": "inactive"}

        if task_definition.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
            return run_task_definition_maintenance(task_definition_id)

        if task_definition.trigger_type != TaskDefinition.TriggerType.CRON:
            logger.info("Task definition %s is not cron-triggered. Skipping cron runner.", task_definition.name)
            return {"status": "skipped", "reason": "wrong_trigger"}

        result = execute_agent_task_definition(task_definition)
        _mark_task_definition_success(task_definition)
        logger.info("Task definition %s executed successfully.", task_definition.name)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("Error executing task definition %s: %s", task_definition_id, e, exc_info=True)
        _mark_task_definition_error(task_definition_id, e)
        if not schedule_trigger_task_retry(
            self,
            e,
            task_definition_id=task_definition_id,
            runner_name="cron",
        ):
            raise


@shared_task(bind=True, name="poll_task_definition_email")
def poll_task_definition_email(self, task_definition_id: int):
    """Poll email and run an agent task definition when new unseen emails arrive."""
    try:
        task_definition = TaskDefinition.objects.select_related(
            "user",
            "agent",
            "email_tool",
        ).get(id=task_definition_id)
        if not task_definition.is_active:
            logger.info("Task definition %s is inactive. Skipping email polling.", task_definition.name)
            return {"status": "skipped", "reason": "inactive"}
        if task_definition.trigger_type != TaskDefinition.TriggerType.EMAIL_POLL:
            logger.info("Task definition %s is not email polling. Skipping email runner.", task_definition.name)
            return {"status": "skipped", "reason": "wrong_trigger"}

        poll_result = poll_new_unseen_email_headers(task_definition)
        headers = poll_result["headers"]
        task_definition.runtime_state = poll_result["state"]

        if poll_result.get("skip_reason") == "backlog_skipped":
            task_definition.save(update_fields=["runtime_state", "updated_at"])
            return {"status": "skipped", "reason": "backlog_skipped"}

        if not headers:
            task_definition.save(update_fields=["runtime_state", "updated_at"])
            return {"status": "noop", "new_email_count": 0}

        variables = build_email_prompt_variables(headers)
        result = execute_agent_task_definition(task_definition, variables=variables)
        _mark_task_definition_success(task_definition)
        task_definition.runtime_state = poll_result["state"]
        task_definition.save(update_fields=["runtime_state", "updated_at"])
        logger.info(
            "Task definition %s executed after email poll (new_email_count=%s).",
            task_definition.name,
            len(headers),
        )
        return {"status": "ok", "new_email_count": len(headers), **result}
    except Exception as e:
        logger.error("Error polling task definition %s: %s", task_definition_id, e, exc_info=True)
        _mark_task_definition_error(task_definition_id, e)
        if not schedule_trigger_task_retry(
            self,
            e,
            task_definition_id=task_definition_id,
            runner_name="email_poll",
        ):
            raise


@shared_task(bind=True, name="run_task_definition_maintenance")
def run_task_definition_maintenance(self, task_definition_id: int):
    """Run a maintenance task definition by dispatching its configured Celery task."""
    try:
        task_definition = TaskDefinition.objects.select_related("user").get(id=task_definition_id)
        if not task_definition.is_active:
            logger.info("Task definition %s is inactive. Skipping maintenance run.", task_definition.name)
            return {"status": "skipped", "reason": "inactive"}
        if task_definition.task_kind != TaskDefinition.TaskKind.MAINTENANCE:
            logger.info("Task definition %s is not maintenance. Skipping maintenance runner.", task_definition.name)
            return {"status": "skipped", "reason": "wrong_kind"}
        if not (task_definition.maintenance_task or "").strip():
            raise ValueError("maintenance_task is required for maintenance task definition")

        task_impl = current_app.tasks.get(task_definition.maintenance_task)
        if not task_impl:
            raise ValueError(f"Unknown maintenance task: {task_definition.maintenance_task}")

        # Maintenance tasks in Nova are currently user-scoped.
        task_impl.delay(user_id=task_definition.user_id)

        _mark_task_definition_success(task_definition)
        logger.info("Maintenance task definition %s dispatched successfully.", task_definition.name)
        return {"status": "ok", "maintenance_task": task_definition.maintenance_task}
    except Exception as e:
        logger.error("Error executing maintenance task definition %s: %s", task_definition_id, e, exc_info=True)
        _mark_task_definition_error(task_definition_id, e)
        raise


@shared_task(bind=True, name="run_scheduled_agent_task")
def run_scheduled_agent_task(self, task_definition_id):
    """Legacy alias kept for backward compatibility with old beat entries."""
    return run_task_definition_cron(task_definition_id)
