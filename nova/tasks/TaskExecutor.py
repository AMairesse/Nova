# nova/tasks/TaskExecutor.py
import datetime as dt
import logging
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from enum import Enum
from typing import Dict, Any
from langgraph.types import Command

from nova.llm.llm_agent import LLMAgent
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import MessageType, Actor
from nova.models.Task import TaskStatus
from nova.tasks.TaskProgressHandler import TaskProgressHandler

logger = logging.getLogger(__name__)


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
    def __init__(self, task, user, thread, agent_config, prompt, *, source_message_id: int | None = None):
        self.task = task
        self.user = user
        self.thread = thread
        self.agent_config = agent_config
        self.prompt = prompt
        self.source_message_id = source_message_id
        self.llm = None
        self.channel_layer = get_channel_layer()
        self.handler = TaskProgressHandler(self.task.id, self.channel_layer)

    async def execute_or_resume(self, interruption_response=None):
        """Main execution method with comprehensive error handling."""
        try:
            await self._initialize_task()
            await self._create_llm_agent()

            if interruption_response:
                # Emit an update
                await self.handler.on_resume_task(interruption_response)
                result = await self.llm.aresume(Command(resume=interruption_response))
            else:
                self.prompt = await self._create_prompt()
                result = await self._run_agent()

            if isinstance(result, dict) and result['__interrupt__']:
                await self._process_interuption(result)
            else:
                await self._process_result(result)
                await self._finalize_task()
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

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

        self.llm = await LLMAgent.create(
            self.user, self.thread, self.agent_config,
            callbacks=[self.handler]
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
