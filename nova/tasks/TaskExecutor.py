# nova/tasks/TaskExecutor.py
import asyncio
import datetime as dt
import logging
import time
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from enum import Enum
from typing import Dict, Any
from langgraph.types import Command

from nova.agent_execution import provider_tools_explicitly_unavailable, requires_tools_for_run
from nova.llm.llm_agent import LLMAgent
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import MessageType, Actor
from nova.models.Task import TaskStatus
from nova.tasks.execution_trace import (
    TaskExecutionTraceHandler,
    build_agent_tool_safe_name,
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
            await self._create_llm_agent()

            if interruption_response:
                # Emit an update
                await self.handler.on_resume_task(interruption_response)
                result = await self.llm.aresume(Command(resume=interruption_response))
            else:
                self.prompt = await self._create_prompt()
                result = await self._run_agent()

            if isinstance(result, dict) and result.get('__interrupt__'):
                await self._process_interuption(result)
            else:
                await self._process_result(result)
                await self._finalize_task()

                # Continuous mode: sub-agents must be stateless.
                # After a successful run, purge all sub-agent checkpoints for this thread,
                # keeping only the main agent checkpoint.
                try:
                    from nova.models.Thread import Thread as ThreadModel
                    if self.thread and self.thread.mode == ThreadModel.Mode.CONTINUOUS:
                        await self._purge_continuous_subagent_checkpoints()
                except Exception:
                    # Best-effort cleanup; never fail the task because of this.
                    pass
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

    async def _purge_continuous_subagent_checkpoints(self) -> None:
        """Delete LangGraph checkpoint state for all sub-agents in a continuous thread.

        Policy: delete all checkpoints for this thread except the main agent's checkpoint.
        We keep CheckpointLink rows; only LangGraph state is deleted.
        """

        if not self.thread or not self.agent_config:
            return

        from nova.models.CheckpointLink import CheckpointLink
        from nova.llm.checkpoints import get_checkpointer

        # Compute which checkpoint_id to keep (main agent)
        def _load_checkpoint_ids():
            keep = (
                CheckpointLink.objects.filter(thread=self.thread, agent=self.agent_config)
                .values_list("checkpoint_id", flat=True)
                .first()
            )
            all_ids = list(
                CheckpointLink.objects.filter(thread=self.thread)
                .exclude(checkpoint_id=keep)
                .values_list("checkpoint_id", flat=True)
            )
            return keep, all_ids

        keep_id, purge_ids = await sync_to_async(_load_checkpoint_ids, thread_sensitive=True)()
        if not purge_ids:
            return

        saver = await get_checkpointer()
        try:
            for ckp_id in purge_ids:
                try:
                    await saver.adelete_thread(ckp_id)
                except Exception:
                    # Best-effort; ignore per-checkpoint failure
                    continue
        finally:
            await saver.conn.close()

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
        """Create and configure the LLM agent."""
        self.task.progress_logs.append({"step": "Creating LLM agent",
                                        "timestamp": str(dt.datetime.now(dt.timezone.utc)), "severity": "info"})
        await sync_to_async(self.task.save, thread_sensitive=False)()
        trace_handler = await self._ensure_trace_handler()

        tools_enabled = True
        provider = await self._get_llm_provider()
        thread_mode = getattr(self.thread, "mode", None)
        if provider_tools_explicitly_unavailable(provider):
            if await sync_to_async(requires_tools_for_run, thread_sensitive=True)(
                self.agent_config,
                thread_mode,
            ):
                raise ValueError(
                    "The selected provider does not support tool use, but this agent depends on tools or sub-agents."
                )
            tools_enabled = False

        self.llm = await LLMAgent.create(
            self.user, self.thread, self.agent_config,
            callbacks=[callback for callback in [self.handler, trace_handler] if callback],
            tools_enabled=tools_enabled,
        )

        # Expose runtime resources to tools via agent._resources
        # Allows built-in tools to emit progress/events over existing WS channels
        try:
            self.llm._resources['channel_layer'] = self.channel_layer
            self.llm._resources['task_id'] = self.task.id
        except Exception:
            # Tools can still fallback to get_channel_layer() if needed
            pass

    async def _create_prompt(self):
        return self.prompt

    async def _create_interaction(self, question: str, schema: Dict[str, Any], agent_name: str):
        """Create the pending Interaction for this task."""
        # Create an Interaction object
        interaction = Interaction(task=self.task, thread=self.thread, agent_config=self.agent_config,
                                  origin_name=agent_name, question=question, schema=schema,
                                  status=InteractionStatus.PENDING)
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

    async def _process_interuption(self, result):
        '''
        This will:
            - upsert an Interaction(PENDING),
            - mark the Task AWAITING_INPUT,
            - emit an update for the frontend
        '''
        # Get interruption's data
        interruption = result['__interrupt__'][0].value
        if not interruption['action'] == 'ask_user':
            raise Exception(f"Unsupported interruption action: {interruption['action']}")
        question = interruption['question']
        schema = interruption['schema']
        agent_name = interruption['agent_name']

        # Create/Update Interaction
        interaction = await self._create_interaction(question, schema, agent_name)

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

    async def _run_agent(self):
        """Execute the LLM agent and return result."""
        self.task.progress_logs.append({"step": "Running AI agent",
                                        "timestamp": str(dt.datetime.now(dt.timezone.utc)), "severity": "info"})
        await sync_to_async(self.task.save, thread_sensitive=False)()

        # Continuous mode: ensure checkpoint state is rebuilt (yesterday/today summaries + today window)
        # before invoking the agent.
        try:
            from nova.models.Thread import Thread as ThreadModel
            if self.thread and self.thread.mode == ThreadModel.Mode.CONTINUOUS:
                from nova.continuous.checkpoint_state import ensure_continuous_checkpoint_state

                rebuilt = await ensure_continuous_checkpoint_state(
                    self.llm,
                    exclude_message_id=self.source_message_id,
                )
                if rebuilt:
                    self.task.progress_logs.append({
                        "step": "Continuous context: checkpoint rebuilt",
                        "timestamp": str(dt.datetime.now(dt.timezone.utc)),
                        "severity": "info",
                    })
                    await sync_to_async(self.task.save, thread_sensitive=False)()
        except Exception:
            # Best-effort: never block agent execution if rebuild fails.
            pass

        return await self.llm.ainvoke(self.prompt)

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
        if self.llm:
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

    async def _load_agent_tool_safe_names(self) -> set[str]:
        if not self.agent_config:
            return set()

        def _load_names():
            related_manager = getattr(self.agent_config, "agent_tools", None)
            if related_manager is None:
                return []
            try:
                raw_names = related_manager.filter(is_tool=True).values_list("name", flat=True)
            except Exception:
                return []
            return [
                build_agent_tool_safe_name(agent_name)
                for agent_name in raw_names
            ]

        names = await sync_to_async(_load_names, thread_sensitive=True)()
        return {
            str(name or "").strip()
            for name in names
            if str(name or "").strip()
        }

    async def _ensure_trace_handler(self, *, resumed: bool = False):
        if self.trace_handler is None:
            self.trace_handler = TaskExecutionTraceHandler(
                self.task,
                ignored_tool_names=await self._load_agent_tool_safe_names(),
            )
        await self.trace_handler.ensure_root_run(
            label=getattr(self.agent_config, "name", "") or "Agent run",
            source_message_id=self.source_message_id,
            agent_id=getattr(self.agent_config, "id", None),
            resumed=resumed,
        )
        return self.trace_handler
