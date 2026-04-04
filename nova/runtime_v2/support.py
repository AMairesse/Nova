from __future__ import annotations

from nova.models.AgentConfig import AgentConfig
from nova.models.Thread import Thread
from .constants import RUNTIME_ENGINE_REACT_TERMINAL_V1, SUPPORTED_PROVIDER_TYPES


def is_react_terminal_runtime(agent_config) -> bool:
    return bool(
        agent_config
        and getattr(agent_config, "runtime_engine", "") == RUNTIME_ENGINE_REACT_TERMINAL_V1
    )


def get_v2_runtime_error(agent_config, *, thread_mode: str | None) -> str | None:
    if not is_react_terminal_runtime(agent_config):
        return None

    if thread_mode != Thread.Mode.THREAD:
        return "React Terminal V1 only supports standard thread mode."

    provider = getattr(agent_config, "llm_provider", None)
    provider_type = str(getattr(provider, "provider_type", "") or "").strip().lower()
    if provider_type not in SUPPORTED_PROVIDER_TYPES:
        return (
            "React Terminal V1 only supports OpenAI-compatible providers "
            "(OpenAI, OpenRouter, LM Studio)."
        )

    if provider is not None and provider.is_capability_explicitly_unavailable("tools"):
        return "The selected provider does not support tool use, but React Terminal V1 requires tools."

    return None


def runtime_choice_labels() -> list[tuple[str, str]]:
    return list(AgentConfig.RuntimeEngine.choices)
