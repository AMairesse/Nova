"""Helpers for resolving provider/agent execution constraints."""

from __future__ import annotations

from dataclasses import dataclass

from nova.models.AgentConfig import AgentConfig
from nova.models.Thread import Thread
from nova.providers.registry import provider_supports_native_response_mode


EXECUTION_MODE_FULL_AGENT = "full_agent"
EXECUTION_MODE_TOOLLESS_GRAPH = "toolless_graph"
EXECUTION_MODE_NATIVE_PROVIDER = "native_provider"
EXECUTION_MODE_BLOCKED_TOOLS = "blocked_tools"


@dataclass(frozen=True)
class ExecutionDecision:
    mode: str
    reason: str = ""


def normalize_requested_response_mode(response_mode: str | None) -> str:
    normalized = str(response_mode or "").strip().lower()
    if normalized in {"", "auto"}:
        return "auto"
    if normalized in {
        AgentConfig.DefaultResponseMode.TEXT,
        AgentConfig.DefaultResponseMode.IMAGE,
        AgentConfig.DefaultResponseMode.AUDIO,
    }:
        return normalized
    return "auto"


def normalize_effective_response_mode(response_mode: str | None) -> str:
    normalized = str(response_mode or "").strip().lower()
    if normalized in {
        AgentConfig.DefaultResponseMode.IMAGE,
        AgentConfig.DefaultResponseMode.AUDIO,
    }:
        return normalized
    return AgentConfig.DefaultResponseMode.TEXT


def resolve_effective_response_mode(agent_config, requested_response_mode: str | None) -> str:
    requested_mode = normalize_requested_response_mode(requested_response_mode)
    if requested_mode != "auto":
        return normalize_effective_response_mode(requested_mode)

    default_mode = getattr(
        agent_config,
        "default_response_mode",
        AgentConfig.DefaultResponseMode.TEXT,
    )
    return normalize_effective_response_mode(default_mode)


def provider_tools_explicitly_unavailable(provider) -> bool:
    if provider is None:
        return False
    return bool(provider.is_capability_explicitly_unavailable("tools"))


def requires_tools_for_run(
    agent_config,
    thread_mode: str | None,
    *,
    explicit_tool_dependency: bool | None = None,
    response_mode: str | None = None,
) -> bool:
    if agent_config is None:
        return False
    if normalize_effective_response_mode(response_mode) in {
        AgentConfig.DefaultResponseMode.IMAGE,
        AgentConfig.DefaultResponseMode.AUDIO,
    }:
        return False
    if explicit_tool_dependency is not None:
        return bool(explicit_tool_dependency) or bool(
            thread_mode == Thread.Mode.CONTINUOUS and not getattr(agent_config, "is_tool", False)
        )
    return bool(agent_config.requires_tools_for_thread_mode(thread_mode))


def resolve_execution_mode(
    agent_config,
    *,
    thread_mode: str | None,
    response_mode: str = "text",
    has_pdf_input: bool = False,
) -> ExecutionDecision:
    provider = getattr(agent_config, "llm_provider", None)
    provider_type = getattr(provider, "provider_type", "")
    effective_response_mode = resolve_effective_response_mode(agent_config, response_mode)

    if (
        provider_type == "openrouter" and has_pdf_input
    ) or (
        provider_supports_native_response_mode(provider, effective_response_mode)
        and effective_response_mode in {"image", "audio"}
    ):
        return ExecutionDecision(EXECUTION_MODE_NATIVE_PROVIDER)

    if provider_tools_explicitly_unavailable(provider):
        if requires_tools_for_run(
            agent_config,
            thread_mode,
            response_mode=effective_response_mode,
        ):
            return ExecutionDecision(
                EXECUTION_MODE_BLOCKED_TOOLS,
                "provider_tools_unsupported",
            )
        return ExecutionDecision(EXECUTION_MODE_TOOLLESS_GRAPH)

    return ExecutionDecision(EXECUTION_MODE_FULL_AGENT)
