# nova/tasks/tasks.py
import asyncio
import datetime as dt
import logging
import time
from asgiref.sync import sync_to_async, async_to_sync
from celery import current_app
from celery import shared_task
from channels.layers import get_channel_layer

from django.contrib.auth.models import User
from django.utils import timezone
from langchain_core.messages import HumanMessage

from nova.models.AgentConfig import AgentConfig
from nova.models.Interaction import Interaction
from nova.models.Message import Message
from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.file_utils import download_file_content
from nova.multimodal_prompts import (
    build_multimodal_intro_text,
    build_multimodal_prompt_content,
)
from nova.providers.registry import create_provider_llm
from nova.turn_inputs import load_message_turn_inputs
from nova.tasks.email_polling import poll_new_unseen_email_headers
from nova.tasks.task_definition_runner import (
    build_email_prompt_variables,
    execute_agent_task_definition,
)
from nova.thread_titles import is_default_thread_subject, normalize_generated_thread_title
from nova.utils import strip_thinking_blocks, markdown_to_html
from nova.runtime.task_executor import (
    ReactTerminalSummarizationTaskExecutor,
    ReactTerminalTaskExecutor,
)

logger = logging.getLogger(__name__)

TRIGGER_TASK_MAX_RETRIES = 5
TRIGGER_TASK_RETRY_BASE_SECONDS = 30
TRIGGER_TASK_RETRY_MAX_SECONDS = 15 * 60
THREAD_TITLE_PROMPT = (
    "Generate a concise thread title (2-6 words) from this conversation excerpt. "
    "Use the same language as the conversation. "
    "Return title only, with no punctuation wrapper and no explanation."
)


async def build_source_message_prompt(
    source_message: Message,
    *,
    provider=None,
    fallback_prompt: str = "",
):
    """Build the runtime user turn payload from a stored source message."""
    source_message_id = getattr(source_message, "pk", None) or getattr(source_message, "id", None)
    prompt_inputs = await load_message_turn_inputs(source_message)
    if not prompt_inputs:
        return source_message.text or fallback_prompt or ""

    intro_text = build_multimodal_intro_text(
        source_message.text or fallback_prompt,
        prompt_inputs,
        empty_text_style="analysis",
        singular_heading="Attached file:",
        plural_heading="Attached files:",
    )
    return await build_multimodal_prompt_content(
        prompt_inputs,
        intro_text=intro_text,
        provider=provider,
        content_downloader=download_file_content,
        log_subject=f"message {source_message_id}",
        include_missing_file_summary=True,
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


def execute_agent_task_with_executor(
    task: Task,
    user: User,
    thread: Thread,
    agent_config: AgentConfig | None,
    prompt_text: str,
    *,
    source_message_id: int | None = None,
    push_notifications_enabled: bool = True,
) -> None:
    """Run a task execution with the React Terminal executor in a synchronous context."""
    executor = ReactTerminalTaskExecutor(
        task,
        user,
        thread,
        agent_config,
        prompt_text,
        source_message_id=source_message_id,
        push_notifications_enabled=push_notifications_enabled,
    )
    asyncio.run(executor.execute_or_resume())


def create_and_dispatch_agent_task(
    *,
    user: User,
    thread: Thread,
    agent_config: AgentConfig | None,
    source_message_id: int,
    dispatcher_task,
) -> Task:
    """Create a pending task and enqueue async execution for an existing user message."""
    task = Task.objects.create(
        user=user,
        thread=thread,
        agent_config=agent_config,
        status=TaskStatus.PENDING,
        progress_logs=[
            {
                "step": "Task queued for dispatch",
                "timestamp": str(dt.datetime.now(dt.timezone.utc)),
                "severity": "info",
            }
        ],
    )

    dispatcher_task.delay(
        task.id,
        user.id,
        thread.id,
        agent_config.id if agent_config else None,
        source_message_id,
    )
    return task


@shared_task(bind=True, name="generate_thread_title")
def generate_thread_title_task(
    self,
    *,
    thread_id: int,
    user_id: int,
    agent_config_id: int,
    source_task_id: int | None = None,
):
    """Generate a short thread title asynchronously without touching runtime session state."""
    title_task_start = time.perf_counter()
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

        response = asyncio.run(
            llm.ainvoke(
                [HumanMessage(content=_build_thread_title_prompt(messages))],
            )
        )

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
        duration_ms = int((time.perf_counter() - title_task_start) * 1000)
        logger.debug(
            "Thread title generation completed (thread_id=%s, source_task_id=%s, duration=%sms).",
            thread_id,
            source_task_id,
            duration_ms,
        )
        return {"status": "ok", "thread_id": thread_id, "thread_subject": normalized_title}
    except Exception as e:
        logger.error("Thread title generation failed for thread %s: %s", thread_id, e, exc_info=True)
        raise


@shared_task(bind=True, name="run_ai_task")
def run_ai_task_celery(self, task_pk, user_pk, thread_pk, agent_pk, message_pk):
    """
    Optimized Celery task with batched database queries and the React Terminal executor.
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

        execute_agent_task_with_executor(
            task,
            user,
            thread,
            agent_config,
            prompt_text,
            source_message_id=message.id,
        )

    except (
        Task.DoesNotExist,
        User.DoesNotExist,
        Thread.DoesNotExist,
        AgentConfig.DoesNotExist,
        Message.DoesNotExist,
    ) as e:
        logger.warning(
            "Skipping Celery task %s because a runtime object is missing: %s",
            task_pk,
            e,
        )
        return {"status": "skipped", "reason": "missing_runtime_object"}
    except Exception as e:
        logger.error(f"Celery task {task_pk} failed: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="resume_ai_task")
def resume_ai_task_celery(self, interaction_pk: int):
    """
    Resume an agent execution after user input.
    Uses the same thread and streams via the same WS group (task_id).
    """
    try:
        interaction = Interaction.objects.select_related(
            'task',
            'task__user',
            'thread',
            'agent_config',
            'agent_config__llm_provider',
        ).get(pk=interaction_pk)
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
            'resume_context': dict(getattr(interaction, "resume_context", {}) or {}),
        }

        executor = ReactTerminalTaskExecutor(task, user, thread, agent_config, interaction)
        asyncio.run(executor.execute_or_resume(interruption_response))

    except Interaction.DoesNotExist:
        logger.warning(
            "Skipping resume_ai_task for interaction %s because it no longer exists.",
            interaction_pk,
        )
        return {"status": "skipped", "reason": "missing_interaction"}
    except Exception as e:
        logger.error(f"Celery resume_ai_task for interaction {interaction_pk} failed: {e}")
        raise self.retry(countdown=30, exc=e)


@shared_task(bind=True, name="summarize_thread_task")
def summarize_thread_task(self, thread_id, user_id, agent_config_id, task_id,
                          include_sub_agents=False, sub_agent_ids=None):
    """
    Celery task to manually summarize a thread with React Terminal compaction.
    """
    try:
        # Get objects (sync database access)
        thread = Thread.objects.get(id=thread_id, user_id=user_id)
        user = User.objects.get(id=user_id)
        agent_config = AgentConfig.objects.get(id=agent_config_id, user=user)
        task = Task.objects.get(id=task_id)

        executor = ReactTerminalSummarizationTaskExecutor(
            task,
            user,
            thread,
            agent_config,
        )
        asyncio.run(executor.execute())

    except Exception as e:
        logger.error(f"Summarization task failed for thread {thread_id}: {e}")
        # Let Celery handle retry logic
        raise self.retry(countdown=60, exc=e)


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


def _handle_missing_task_definition(task_definition_id: int, *, runner_name: str):
    deleted = TaskDefinition.cleanup_periodic_task_for_id(task_definition_id)
    if deleted:
        logger.warning(
            "Task definition %s is missing during %s. Deleted %s stale periodic task(s).",
            task_definition_id,
            runner_name,
            deleted,
        )
    else:
        logger.warning(
            "Task definition %s is missing during %s. No stale periodic task was found.",
            task_definition_id,
            runner_name,
        )
    return {"status": "skipped", "reason": "missing_task_definition"}


@shared_task(bind=True, name="run_task_definition_cron")
def run_task_definition_cron(self, task_definition_id: int):
    """Run an agent task definition configured with a cron trigger."""
    try:
        task_definition = TaskDefinition.objects.select_related(
            "user",
            "agent",
            "agent__llm_provider",
        ).get(id=task_definition_id)
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
    except TaskDefinition.DoesNotExist:
        return _handle_missing_task_definition(task_definition_id, runner_name="cron")
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
            "agent__llm_provider",
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
    except TaskDefinition.DoesNotExist:
        return _handle_missing_task_definition(task_definition_id, runner_name="email_poll")
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
    except TaskDefinition.DoesNotExist:
        return _handle_missing_task_definition(task_definition_id, runner_name="maintenance")
    except Exception as e:
        logger.error("Error executing maintenance task definition %s: %s", task_definition_id, e, exc_info=True)
        _mark_task_definition_error(task_definition_id, e)
        raise
