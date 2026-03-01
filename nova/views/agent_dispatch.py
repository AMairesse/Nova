from django.shortcuts import get_object_or_404

from nova.models.AgentConfig import AgentConfig
from nova.models.Thread import Thread
from nova.tasks.tasks import create_and_dispatch_agent_task


def resolve_selected_or_default_agent(user, selected_agent: str | None):
    if selected_agent:
        return get_object_or_404(AgentConfig, id=selected_agent, user=user)
    return getattr(getattr(user, "userprofile", None), "default_agent", None)


def enqueue_message_agent_task(
    *,
    user,
    thread: Thread,
    agent_config,
    source_message_id: int,
    dispatcher_task,
):
    return create_and_dispatch_agent_task(
        user=user,
        thread=thread,
        agent_config=agent_config,
        source_message_id=source_message_id,
        dispatcher_task=dispatcher_task,
    )
