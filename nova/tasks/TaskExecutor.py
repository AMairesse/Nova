# nova/tasks/TaskExecutor.py
import asyncio
import datetime as dt
import logging
import time
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from enum import Enum
from typing import Dict, Any

from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import MessageType, Actor
from nova.models.Task import TaskStatus
from nova.tasks.execution_trace import (
    TaskExecutionTraceHandler,
)
from nova.tasks.TaskProgressHandler import TaskProgressHandler

logger = logging.getLogger(__name__)
LLM_CLEANUP_TIMEOUT_SECONDS = 5.0
_UNSET = object()


class TaskErrorCategory(Enum):
    AGENT_FAILURE = "agent_failure"
    TOOL_FAILURE = "tool_failure"
    SYSTEM_ERROR = "system_error"
    VALIDATION_ERROR = "validation_error"
    NETWORK_ERROR = "network_error"


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
    def __init__(
        self,
        task,
        user,
        thread,
        agent_config,
        prompt,
        *,
        source_message_id: int | None = None,
        push_notifications_enabled: bool = True,
    ):
        self.task = task
        self.user = user
        self.thread = thread
        self.agent_config = agent_config
        self.prompt = prompt
        self.source_message_id = source_message_id
        self.llm = None
        self.channel_layer = get_channel_layer()
        self.handler = TaskProgressHandler(
            self.task.id,
            self.channel_layer,
            user_id=getattr(self.user, "id", None),
            thread_id=getattr(self.thread, "id", None),
            thread_mode=getattr(self.thread, "mode", None),
            initial_streamed_markdown=getattr(self.task, "streamed_markdown", "") or "",
            push_notifications_enabled=push_notifications_enabled,
        )
        self.trace_handler = None
        self._llm_provider = _UNSET

    async def _get_llm_provider(self):
        """Resolve agent_config.llm_provider without sync ORM access inside async code."""
        if self._llm_provider is not _UNSET:
            return self._llm_provider

        if not self.agent_config:
            self._llm_provider = None
            return None

        state = getattr(self.agent_config, "_state", None)
        fields_cache = getattr(state, "fields_cache", None)
        if isinstance(fields_cache, dict) and "llm_provider" not in fields_cache:
            provider = await sync_to_async(
                lambda: self.agent_config.llm_provider,
                thread_sensitive=True,
            )()
        else:
            provider = getattr(self.agent_config, "llm_provider", None)

        self._llm_provider = provider
        return provider

    async def execute_or_resume(self, interruption_response=None):
        """Main execution method with comprehensive error handling."""
        try:
            await self._initialize_task(interruption_response=interruption_response)
            await self._ensure_trace_handler(resumed=bool(interruption_response))
            if interruption_response and self.trace_handler:
                await self.trace_handler.resolve_latest_interaction(
                    interaction_status=str(interruption_response.get("interaction_status") or ""),
                    answer_preview=interruption_response.get("user_response"),
                )
            await self._create_llm_agent()

            if interruption_response:
                # Emit an update
                await self.handler.on_resume_task(interruption_response)
                result = await self._resume_agent(interruption_response)
            else:
                self.prompt = await self._create_prompt()
                result = await self._run_agent()

            interruption = self._extract_interruption_payload(result)
            if interruption is not None:
                await self._process_interruption_payload(interruption)
            else:
                await self._process_result(result)
                await self._finalize_task()
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

    async def _resume_agent(self, interruption_response):
        raise NotImplementedError

    def _extract_interruption_payload(self, result):
        if not (isinstance(result, dict) and result.get('__interrupt__')):
            return None
        interruption = result['__interrupt__'][0].value
        return {
            "action": interruption.get("action"),
            "question": interruption.get("question"),
            "schema": interruption.get("schema") or {},
            "agent_name": interruption.get("agent_name") or "",
            "resume_context": interruption.get("resume_context") or {},
        }

    async def _initialize_task(self, interruption_response=None):
        """Initialize task state and logging."""
        self.task.status = TaskStatus.RUNNING
        if interruption_response:
            step = "Resuming after user input"
        else:
            step = "Initializing AI task"
        self.task.progress_logs.append({"step": step, "timestamp": str(dt.datetime.now(dt.timezone.utc)),
                                        "severity": "info"})
        await sync_to_async(self.task.save, thread_sensitive=False)()

    async def _create_llm_agent(self):
        raise NotImplementedError

    async def _create_prompt(self):
        return self.prompt

    async def _create_interaction(
        self,
        question: str,
        schema: Dict[str, Any],
        agent_name: str,
        *,
        resume_context: Dict[str, Any] | None = None,
    ):
        """Create the pending Interaction for this task."""
        # Create an Interaction object
        interaction = Interaction(task=self.task, thread=self.thread, agent_config=self.agent_config,
                                  origin_name=agent_name, question=question, schema=schema,
                                  status=InteractionStatus.PENDING,
                                  resume_context=resume_context or {})
        await sync_to_async(interaction.full_clean, thread_sensitive=False)()
        await sync_to_async(interaction.save, thread_sensitive=False)()

        # Create a message for the interaction question
        question_text = f"**{agent_name} asks:** {question}"
        message = await sync_to_async(self.thread.add_message, thread_sensitive=False)(question_text, Actor.SYSTEM,
                                                                                       MessageType.INTERACTION_QUESTION,
                                                                                       interaction)

        # Store the message in the interaction for reference
        interaction.question_message = message
        await sync_to_async(interaction.save, thread_sensitive=False)()

        return interaction

    async def _process_interruption_payload(self, interruption):
        """
        Handle a runtime-agnostic interruption payload and suspend the task.
        """
        if not interruption['action'] == 'ask_user':
            raise Exception(f"Unsupported interruption action: {interruption['action']}")
        question = interruption['question']
        schema = interruption['schema']
        agent_name = interruption['agent_name']
        resume_context = interruption.get("resume_context") or {}

        # Create/Update Interaction
        interaction = await self._create_interaction(
            question,
            schema,
            agent_name,
            resume_context=resume_context,
        )

        if self.trace_handler:
            await self.trace_handler.record_interaction(
                question=question,
                schema=schema,
                agent_name=agent_name,
            )
            await self.trace_handler.mark_root_awaiting_input()

        # Mark task awaiting input
        self.task.progress_logs.append({"step": f"Waiting for user input: {question[:120]}",
                                        "timestamp": str(dt.datetime.now(dt.timezone.utc)), "severity": "info"})
        self.task.status = TaskStatus.AWAITING_INPUT
        await sync_to_async(self.task.save, thread_sensitive=False)()

        # Emit an update
        await self.handler.on_interrupt(interaction.id, question, schema, agent_name)

    async def _process_interuption(self, result):
        '''
        Backward-compatible wrapper around the new runtime-agnostic interruption payload flow.
        '''
        interruption = self._extract_interruption_payload(result)
        if interruption is None:
            raise Exception("Unsupported interruption payload.")
        await self._process_interruption_payload(interruption)

    async def _run_agent(self):
        raise NotImplementedError

    async def _finalize_task(self):
        """Finalize the task as completed."""
        self.task.progress_logs.append({"step": "Task completed successfully",
                                        "timestamp": str(dt.datetime.now(dt.timezone.utc)), "severity": "success"})

        self.task.status = TaskStatus.COMPLETED
        await sync_to_async(self.task.save, thread_sensitive=False)()

        await self.handler.on_task_complete(self.task.result, self.thread.id, self.thread.subject)

    async def _handle_execution_error(self, error):
        """Handle execution errors with proper categorization."""
        error_category = self._categorize_error(error)

        error_msg = f"{error_category.value}: {str(error)}"
        logger.error(f"Task {self.task.id} failed: {error_msg}")

        # Update task state
        self.task.status = TaskStatus.FAILED
        self.task.result = error_msg

        # Enhanced error logging
        error_log = {"step": f"Execution failed: {str(error)}", "category": error_category.value,
                     "timestamp": str(dt.datetime.now(dt.timezone.utc)), "severity": "error",
                     "error_details": {"type": type(error).__name__, "message": str(error)}}
        self.task.progress_logs.append(error_log)
        await sync_to_async(self.task.save, thread_sensitive=False)()

        try:
            trace_handler = await self._ensure_trace_handler()
            if trace_handler:
                await trace_handler.fail_root_run(
                    str(error),
                    category=error_category.value,
                )
        except Exception:
            logger.exception("Failed to persist execution trace error for task %s", getattr(self.task, "id", None))

        # Publish error update
        if self.handler:
            try:
                await self.handler.on_error(error_msg, error_category.value)
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
        if self.llm and hasattr(self.llm, "cleanup_runtime"):
            cleanup_start = time.perf_counter()
            try:
                await asyncio.wait_for(
                    self.llm.cleanup_runtime(),
                    timeout=LLM_CLEANUP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("Timed out while cleaning up LLM resources")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup LLM: {cleanup_error}")
            else:
                duration_ms = int((time.perf_counter() - cleanup_start) * 1000)
                logger.debug(
                    "TaskExecutor cleanup finished for task %s in %sms.",
                    getattr(self.task, "id", None),
                    duration_ms,
                )

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

    async def _ensure_trace_handler(self, *, resumed: bool = False):
        if self.trace_handler is None:
            self.trace_handler = TaskExecutionTraceHandler(self.task)
        await self.trace_handler.ensure_root_run(
            label=getattr(self.agent_config, "name", "") or "Agent run",
            source_message_id=self.source_message_id,
            agent_id=getattr(self.agent_config, "id", None),
            resumed=resumed,
        )
        return self.trace_handler
