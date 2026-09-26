from __future__ import annotations

from nova.agent_markdown import render_agent_markdown
from nova.message_utils import annotate_user_message
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Message


def build_task_message_payload(message) -> dict:
    annotate_user_message(message)
    internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
    display_text = internal_data.get("display_markdown") or message.text or ""
    return {
        "id": message.id,
        "text": message.text,
        "actor": message.actor,
        "internal_data": internal_data,
        "created_at": str(message.created_at),
        "rendered_html": render_agent_markdown(
            display_text,
            user=message.user,
            thread=message.thread,
        ),
    }


def build_task_snapshot(task) -> dict:
    """Build the persisted state needed to recover a task after reconnect."""
    messages = Message.objects.select_related("thread", "user").filter(
        user_id=task.user_id,
        thread_id=task.thread_id,
        internal_data__trace_task_id=task.id,
    ).order_by("created_at", "id")

    interactions = Interaction.objects.filter(
        task_id=task.id,
        thread_id=task.thread_id,
    ).order_by("created_at", "id")

    return {
        "type": "task_snapshot",
        "task_id": task.id,
        "thread_id": task.thread_id,
        "status": task.status,
        "current_response": task.current_response,
        "last_progress": (task.progress_logs or [])[-1] if task.progress_logs else None,
        "result": task.result,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "messages": [build_task_message_payload(message) for message in messages],
        "interactions": [_interaction_payload(interaction) for interaction in interactions],
    }


def _interaction_payload(interaction) -> dict:
    if interaction.status == InteractionStatus.PENDING:
        return {
            "type": "user_prompt",
            "interaction_id": interaction.id,
            "question": interaction.question,
            "schema": interaction.schema or {},
            "origin_name": interaction.origin_name,
            "thread_id": interaction.thread_id,
        }
    return {
        "type": "interaction_update",
        "interaction_id": interaction.id,
        "interaction_status": interaction.status,
        "thread_id": interaction.thread_id,
    }
