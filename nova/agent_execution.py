"""Helpers for resolving provider/agent execution constraints."""

from __future__ import annotations

from dataclasses import dataclass


EXECUTION_MODE_FULL_AGENT = "full_agent"
EXECUTION_MODE_TOOLLESS_GRAPH = "toolless_graph"
EXECUTION_MODE_NATIVE_PROVIDER = "native_provider"
EXECUTION_MODE_BLOCKED_TOOLS = "blocked_tools"


@dataclass(frozen=True)
class ExecutionDecision:
    mode: str
    reason: str = ""


def provider_tools_explicitly_unavailable(provider) -> bool:
    if provider is None:
        return False
    return bool(provider.is_capability_explicitly_unavailable("tools"))


def requires_tools_for_run(agent_config, thread_mode: str | None) -> bool:
    if agent_config is None:
        return False
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
    normalized_response_mode = str(response_mode or "text").strip().lower() or "text"

    if provider_type == "openrouter" and (
        has_pdf_input or normalized_response_mode in {"image", "audio"}
    ):
        return ExecutionDecision(EXECUTION_MODE_NATIVE_PROVIDER)

    if provider_tools_explicitly_unavailable(provider):
        if requires_tools_for_run(agent_config, thread_mode):
            return ExecutionDecision(
                EXECUTION_MODE_BLOCKED_TOOLS,
                "provider_tools_unsupported",
            )
        return ExecutionDecision(EXECUTION_MODE_TOOLLESS_GRAPH)

    return ExecutionDecision(EXECUTION_MODE_FULL_AGENT)
