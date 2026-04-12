from __future__ import annotations

from nova.agent_execution import (
    provider_tools_explicitly_unavailable,
    requires_tools_for_run,
    resolve_effective_response_mode,
)
from nova.models.Thread import Thread

def get_runtime_error(
    agent_config,
    *,
    thread_mode: str | None,
    response_mode: str | None = None,
    explicit_tool_dependency: bool | None = None,
) -> str | None:
    if thread_mode not in {Thread.Mode.THREAD, Thread.Mode.CONTINUOUS}:
        return "Nova runtime only supports standard and continuous thread modes."

    provider = getattr(agent_config, "llm_provider", None)
    effective_response_mode = resolve_effective_response_mode(agent_config, response_mode)
    if provider_tools_explicitly_unavailable(provider) and requires_tools_for_run(
        agent_config,
        thread_mode,
        explicit_tool_dependency=explicit_tool_dependency,
        response_mode=effective_response_mode,
    ):
        return "The selected provider does not support tool use, but this agent depends on tools or sub-agents."

    return None
