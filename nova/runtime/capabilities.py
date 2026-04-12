from __future__ import annotations

from dataclasses import dataclass, field

from nova.plugins.base import ResolvedInternalPlugin
from nova.plugins.registry import get_plugin, resolve_active_plugins


@dataclass(slots=True)
class TerminalCapabilities:
    plugins: dict[str, ResolvedInternalPlugin] = field(default_factory=dict)
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

    def __post_init__(self):
        if self.plugins:
            self._hydrate_legacy_fields_from_plugins()
        else:
            self._synthesize_plugins_from_legacy_fields()

    def _set_plugin(self, plugin_id: str, features: dict) -> None:
        descriptor = get_plugin(plugin_id)
        if descriptor is None:
            return
        self.plugins[plugin_id] = ResolvedInternalPlugin(descriptor=descriptor, features=dict(features))

    def _synthesize_plugins_from_legacy_fields(self) -> None:
        synthesized: dict[str, ResolvedInternalPlugin] = {}
        self.plugins = synthesized
        self._set_plugin("terminal", {})
        self._set_plugin("history", {})
        if self.mcp_tools:
            self._set_plugin("mcp", {"tools": list(self.mcp_tools)})
        if self.api_tools:
            self._set_plugin("api", {"tools": list(self.api_tools)})
        if self.email_tools:
            self._set_plugin("mail", {"tools": list(self.email_tools)})
        if self.caldav_tools:
            self._set_plugin("calendar", {"tools": list(self.caldav_tools)})
        if self.searxng_tool is not None:
            self._set_plugin("search", {"tool": self.searxng_tool})
        if self.webdav_tools:
            self._set_plugin("webdav", {"tools": list(self.webdav_tools)})
        if self.browser_tool is not None:
            self._set_plugin("browser", {"tool": self.browser_tool})
            self._set_plugin("downloads", {"tool": self.browser_tool})
        if self.webapp_tool is not None:
            self._set_plugin("webapp", {"tool": self.webapp_tool})
        if self.code_execution_tool is not None:
            self._set_plugin("python", {"tool": self.code_execution_tool})
        if self.date_time_tool is not None:
            self._set_plugin("datetime", {"tool": self.date_time_tool})
        if self.memory_tool is not None:
            self._set_plugin("memory", {"tool": self.memory_tool})

    def _hydrate_legacy_fields_from_plugins(self) -> None:
        self.plugins.setdefault("terminal", ResolvedInternalPlugin(descriptor=get_plugin("terminal"), features={}))
        self.plugins.setdefault("history", ResolvedInternalPlugin(descriptor=get_plugin("history"), features={}))
        self.mcp_tools = list(self._plugin_feature("mcp", "tools", []))
        self.api_tools = list(self._plugin_feature("api", "tools", []))
        self.email_tools = list(self._plugin_feature("mail", "tools", []))
        self.caldav_tools = list(self._plugin_feature("calendar", "tools", []))
        self.searxng_tool = self._plugin_feature("search", "tool")
        self.webdav_tools = list(self._plugin_feature("webdav", "tools", []))
        self.browser_tool = self._plugin_feature("browser", "tool")
        self.webapp_tool = self._plugin_feature("webapp", "tool")
        self.code_execution_tool = self._plugin_feature("python", "tool")
        self.date_time_tool = self._plugin_feature("datetime", "tool")
        self.memory_tool = self._plugin_feature("memory", "tool")

    def _plugin_feature(self, plugin_id: str, key: str, default=None):
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return default
        return plugin.features.get(key, default)

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
        seen = set(families)
        for plugin in self.plugins.values():
            for family in plugin.descriptor.command_families:
                if family in seen:
                    continue
                families.append(family)
                seen.add(family)
        return families


def resolve_terminal_capabilities(agent_config) -> TerminalCapabilities:
    tools = list(agent_config.tools.order_by("id"))
    subagents = list(
        agent_config.agent_tools.filter(
            is_tool=True,
        ).select_related("llm_provider")
    )

    plugins = resolve_active_plugins(tools=tools, subagents=subagents)
    return TerminalCapabilities(
        plugins=plugins,
        subagents=subagents,
    )
