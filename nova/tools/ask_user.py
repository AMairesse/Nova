# nova/tools/ask_user.py
from __future__ import annotations

from typing import Optional, Dict, Any
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.utils.translation import gettext_lazy as _
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.llm.exceptions import AskUserPause
from nova.models.models import (
    Interaction, InteractionStatus, TaskStatus, Agent, Task
)


async def _get_current_task(agent: LLMAgent) -> Task:
    """Retrieve the current Task injected by the executor."""
    task = getattr(agent, "_current_task", None)
    if task is None:
        raise RuntimeError("Current task context is not available on the agent.")
    return task


async def _ensure_single_pending(task: Task) -> Optional[Interaction]:
    """Return existing pending interaction for the task, if any."""
    return await sync_to_async(
        lambda: Interaction.objects.filter(task=task, status=InteractionStatus.PENDING).first(),
        thread_sensitive=False
    )()


async def _create_or_update_interaction(
    task: Task,
    agent: LLMAgent,
    question: str,
    schema: Optional[Dict[str, Any]],
    origin_name: Optional[str],
) -> Interaction:
    """Create or update the pending Interaction for this task."""
    thread = agent.thread
    origin_agent = agent.agent_config if isinstance(agent.agent_config, Agent) else None
    origin_display = origin_name or (origin_agent.name if origin_agent else "Agent")

    existing = await _ensure_single_pending(task)
    if existing:
        existing.question = question
        existing.schema = schema or {}
        existing.origin_name = origin_display
        await sync_to_async(existing.save, thread_sensitive=False)(update_fields=["question", "schema",
                                                                                  "origin_name", "updated_at"])
        # Update the associated message if it exists
        if hasattr(existing, 'messages') and existing.messages.exists():
            question_message = existing.messages.filter(message_type='interaction_question').first()
            if question_message:
                question_message.text = question
                await sync_to_async(question_message.save, thread_sensitive=False)()
        return existing

    interaction = Interaction(
        task=task,
        thread=thread,
        agent=origin_agent,
        origin_name=origin_display,
        question=question,
        schema=schema or {},
        status=InteractionStatus.PENDING,
    )
    await sync_to_async(interaction.full_clean, thread_sensitive=False)()
    await sync_to_async(interaction.save, thread_sensitive=False)()

    # Create a message for the interaction question
    from nova.models.Message import MessageType, Actor
    question_text = f"**{origin_display} asks:** {question}"
    message = await sync_to_async(
        thread.add_message,
        thread_sensitive=False
    )(question_text, Actor.SYSTEM, MessageType.INTERACTION_QUESTION, interaction)

    # Store the message in the interaction for reference
    interaction.question_message = message
    await sync_to_async(interaction.save, thread_sensitive=False)()

    return interaction


async def _mark_task_awaiting(task: Task, question: str):
    """Mark task as awaiting input and append a progress log entry."""
    if not task.progress_logs:
        task.progress_logs = []
    task.progress_logs.append({
        "step": f"Waiting for user input: {question[:120]}",
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "severity": "info",
    })
    task.status = TaskStatus.AWAITING_INPUT
    await sync_to_async(task.save, thread_sensitive=False)()


async def _broadcast_user_prompt(task_id: int, payload: Dict[str, Any]):
    """Emit a user_prompt event on the task WS group."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        f"task_{task_id}",
        {
            "type": "task_update",
            "message": {
                "type": "user_prompt",
                **payload,
            },
        },
    )


def _build_tool(agent: LLMAgent) -> StructuredTool:
    """Return the StructuredTool bound to the provided agent."""

    async def _ask_user(
        question: str,
        schema: Optional[Dict[str, Any]] = None,
        origin_name: Optional[str] = None,
    ) -> str:
        """
        Ask a blocking question to the end user.
        This will:
          - upsert an Interaction(PENDING),
          - mark the Task AWAITING_INPUT,
          - emit a WS 'user_prompt',
          - raise AskUserPause to stop the current run.
        """
        # 1) Retrieve task
        task = await _get_current_task(agent)

        # 2) Create/Update Interaction
        interaction = await _create_or_update_interaction(task, agent, question, schema, origin_name)

        # 3) Mark task awaiting input
        await _mark_task_awaiting(task, question)

        # 4) Emit WS event (UI will render an interactive card)
        await _broadcast_user_prompt(task.id, {
            "interaction_id": interaction.id,
            "question": question,
            "schema": schema or {},
            "origin_name": interaction.origin_name,
            "thread_id": agent.thread.id if agent.thread else None,
        })

        # 5) Interrupt execution flow
        raise AskUserPause(interaction_id=interaction.id)

    return StructuredTool.from_function(
        func=None,
        coroutine=_ask_user,
        name="ask_user",
        description=_(
            "Ask the end-user a clarification question and pause execution. "
            "Use when additional information is required to proceed."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to show to the user",
                },
                "schema": {
                    "type": "object",
                    "description": "Optional JSON schema describing expected answer shape",
                },
                "origin_name": {
                    "type": "string",
                    "description": "Optional display name of the asking agent/tool",
                },
            },
            "required": ["question"],
        },
    )


async def get_functions(agent: LLMAgent) -> list[StructuredTool]:
    """Expose ask_user as a single StructuredTool, loaded unconditionally."""
    return [_build_tool(agent)]
