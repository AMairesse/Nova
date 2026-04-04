from __future__ import annotations

from dataclasses import dataclass, field

from .constants import RUNTIME_ENGINE_REACT_TERMINAL_V1


@dataclass(slots=True)
class TerminalCapabilities:
    email_tool: object | None = None
    browser_tool: object | None = None
    code_execution_tool: object | None = None
    subagents: list = field(default_factory=list)

    @property
    def has_email(self) -> bool:
        return self.email_tool is not None

    @property
    def has_web(self) -> bool:
        return self.browser_tool is not None

    @property
    def has_python(self) -> bool:
        return self.code_execution_tool is not None

    @property
    def has_subagents(self) -> bool:
        return bool(self.subagents)

    def enabled_command_families(self) -> list[str]:
        families = ["filesystem", "skills"]
        if self.has_web:
            families.append("web")
        if self.has_email:
            families.append("mail")
        if self.has_python:
            families.append("python")
        return families


def resolve_terminal_capabilities(agent_config) -> TerminalCapabilities:
    tools = list(agent_config.tools.filter(is_active=True))
    subagents = list(
        agent_config.agent_tools.filter(
            is_tool=True,
            runtime_engine=RUNTIME_ENGINE_REACT_TERMINAL_V1,
        ).select_related("llm_provider")
    )

    email_tool = next((tool for tool in tools if tool.tool_subtype == "email"), None)
    browser_tool = next((tool for tool in tools if tool.tool_subtype == "browser"), None)
    code_execution_tool = next((tool for tool in tools if tool.tool_subtype == "code_execution"), None)

    return TerminalCapabilities(
        email_tool=email_tool,
        browser_tool=browser_tool,
        code_execution_tool=code_execution_tool,
        subagents=subagents,
    )
