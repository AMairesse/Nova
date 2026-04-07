from __future__ import annotations

from nova.agent_execution import provider_tools_explicitly_unavailable, requires_tools_for_run
from nova.models.Thread import Thread

def get_runtime_error(agent_config, *, thread_mode: str | None) -> str | None:
    if thread_mode not in {Thread.Mode.THREAD, Thread.Mode.CONTINUOUS}:
        return "React Terminal only supports standard and continuous thread modes."

    provider = getattr(agent_config, "llm_provider", None)
    if provider_tools_explicitly_unavailable(provider) and requires_tools_for_run(agent_config, thread_mode):
        return "The selected provider does not support tool use, but this agent depends on tools or sub-agents."

    return None
