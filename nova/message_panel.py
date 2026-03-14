from __future__ import annotations

from nova.models.AgentConfig import AgentConfig
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.UserObjects import UserProfile


def get_user_default_agent(user):
    try:
        return user.userprofile.default_agent
    except UserProfile.DoesNotExist:
        return None


def get_message_panel_agents(
    user,
    *,
    thread_mode: str,
    selected_agent_id: str | None = None,
):
    user_agents = list(
        AgentConfig.objects.select_related("llm_provider").filter(user=user, is_tool=False)
    )
    for agent in user_agents:
        agent.requires_tools_for_current_thread = agent.requires_tools_for_thread_mode(thread_mode)

    default_agent = None
    if selected_agent_id:
        default_agent = AgentConfig.objects.select_related("llm_provider").filter(
            id=selected_agent_id,
            user=user,
        ).first()
    if not default_agent:
        default_agent = get_user_default_agent(user)

    return user_agents, default_agent


def get_pending_interactions(thread):
    return (
        Interaction.objects.filter(
            thread=thread,
            status=InteractionStatus.PENDING,
        )
        .select_related("task", "agent_config")
        .order_by("created_at", "id")
    )
