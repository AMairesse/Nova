from __future__ import annotations

from dataclasses import dataclass, field

from .constants import RUNTIME_ENGINE_REACT_TERMINAL_V1


@dataclass(slots=True)
class TerminalCapabilities:
    mcp_tools: list = field(default_factory=list)
    api_tools: list = field(default_factory=list)
    email_tools: list = field(default_factory=list)
    caldav_tools: list = field(default_factory=list)
    searxng_tool: object | None = None
    webdav_tools: list = field(default_factory=list)
    browser_tool: object | None = None
    webapp_tool: object | None = None
    code_execution_tool: object | None = None
    date_time_tool: object | None = None
    memory_tool: object | None = None
    subagents: list = field(default_factory=list)

    @property
    def has_email(self) -> bool:
        return bool(self.email_tools)

    @property
    def has_mcp(self) -> bool:
        return bool(self.mcp_tools)

    @property
    def has_api(self) -> bool:
        return bool(self.api_tools)

    @property
    def has_multiple_mailboxes(self) -> bool:
        return len(self.email_tools) > 1

    @property
    def has_web(self) -> bool:
        return self.browser_tool is not None

    @property
    def has_webapp(self) -> bool:
        return self.webapp_tool is not None

    @property
    def has_calendar(self) -> bool:
        return bool(self.caldav_tools)

    @property
    def has_multiple_calendar_accounts(self) -> bool:
        return len(self.caldav_tools) > 1

    @property
    def has_search(self) -> bool:
        return self.searxng_tool is not None

    @property
    def has_webdav(self) -> bool:
        return bool(self.webdav_tools)

    @property
    def has_python(self) -> bool:
        return self.code_execution_tool is not None

    @property
    def has_date_time(self) -> bool:
        return self.date_time_tool is not None

    @property
    def has_memory(self) -> bool:
        return self.memory_tool is not None

    @property
    def has_subagents(self) -> bool:
        return bool(self.subagents)

    def enabled_command_families(self) -> list[str]:
        families = ["filesystem", "skills"]
        if self.has_mcp:
            families.append("mcp")
        if self.has_api:
            families.append("api")
        if self.has_search:
            families.append("search")
        if self.has_web:
            families.extend(["browse", "downloads"])
        if self.has_webapp:
            families.append("webapp")
        if self.has_webdav:
            families.append("webdav")
        if self.has_calendar:
            families.append("calendar")
        if self.has_email:
            families.append("mail")
        if self.has_python:
            families.append("python")
        if self.has_date_time:
            families.append("date")
        if self.has_memory:
            families.append("memory")
        return families


def resolve_terminal_capabilities(agent_config) -> TerminalCapabilities:
    tools = list(agent_config.tools.filter(is_active=True).order_by("id"))
    subagents = list(
        agent_config.agent_tools.filter(
            is_tool=True,
            runtime_engine=RUNTIME_ENGINE_REACT_TERMINAL_V1,
        ).select_related("llm_provider")
    )

    mcp_tools = [tool for tool in tools if getattr(tool, "tool_type", "") == "mcp"]
    api_tools = [tool for tool in tools if getattr(tool, "tool_type", "") == "api"]
    email_tools = [tool for tool in tools if tool.tool_subtype == "email"]
    caldav_tools = [tool for tool in tools if tool.tool_subtype == "caldav"]
    searxng_tool = next((tool for tool in tools if tool.tool_subtype == "searxng"), None)
    webdav_tools = [tool for tool in tools if tool.tool_subtype == "webdav"]
    browser_tool = next((tool for tool in tools if tool.tool_subtype == "browser"), None)
    webapp_tool = next((tool for tool in tools if tool.tool_subtype == "webapp"), None)
    code_execution_tool = next((tool for tool in tools if tool.tool_subtype == "code_execution"), None)
    date_time_tool = next((tool for tool in tools if tool.tool_subtype == "date"), None)
    memory_tool = next((tool for tool in tools if tool.tool_subtype == "memory"), None)

    return TerminalCapabilities(
        mcp_tools=mcp_tools,
        api_tools=api_tools,
        email_tools=email_tools,
        caldav_tools=caldav_tools,
        searxng_tool=searxng_tool,
        webdav_tools=webdav_tools,
        browser_tool=browser_tool,
        webapp_tool=webapp_tool,
        code_execution_tool=code_execution_tool,
        date_time_tool=date_time_tool,
        memory_tool=memory_tool,
        subagents=subagents,
    )
