from __future__ import annotations

from dataclasses import dataclass

from nova.continuous.utils import append_continuous_user_message, enqueue_continuous_followups
from nova.models.AgentConfig import AgentConfig
from nova.models.Task import Task, TaskStatus


@dataclass(frozen=True)
class ContinuousIngressResult:
    thread_id: int
    task_id: int
    message_id: int
    day_segment_id: int
    day_label: str
    opened_new_day: bool


def ingest_continuous_user_message(
    *,
    user,
    message_text: str,
    run_ai_task,
    selected_agent_id: int | None = None,
    source_channel: str = "web",
    source_transport: str = "web_ui",
    source_external_message_id: str | None = None,
) -> ContinuousIngressResult:
    """Canonical entrypoint to feed the shared continuous thread from any channel."""

    thread, msg, seg, day_label, opened_new_day = append_continuous_user_message(user, message_text)

    source_payload = {
        "channel": source_channel,
        "transport": source_transport,
    }
    if source_external_message_id:
        source_payload["external_message_id"] = source_external_message_id

    msg.internal_data = {
        **(msg.internal_data or {}),
        "source": source_payload,
    }
    msg.save(update_fields=["internal_data"])

    agent_config = None
    if selected_agent_id:
        agent_config = AgentConfig.objects.filter(id=selected_agent_id, user=user).first()
    if not agent_config:
        agent_config = getattr(getattr(user, "userprofile", None), "default_agent", None)

    task = Task.objects.create(user=user, thread=thread, agent_config=agent_config, status=TaskStatus.PENDING)

    run_ai_task.delay(task.id, user.id, thread.id, agent_config.id if agent_config else None, msg.id)

    enqueue_continuous_followups(
        user=user,
        thread=thread,
        day_label=day_label,
        segment=seg,
        opened_new_day=opened_new_day,
        source=f"continuous_ingest:{source_channel}",
    )

    return ContinuousIngressResult(
        thread_id=thread.id,
        task_id=task.id,
        message_id=msg.id,
        day_segment_id=seg.id,
        day_label=day_label.isoformat(),
        opened_new_day=opened_new_day,
    )
