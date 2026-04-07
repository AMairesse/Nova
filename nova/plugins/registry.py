from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .api import PLUGIN as API_PLUGIN
from .base import InternalPluginDescriptor, ResolvedInternalPlugin
from .browser import PLUGIN as BROWSER_PLUGIN
from .calendar import PLUGIN as CALENDAR_PLUGIN
from .datetime import PLUGIN as DATETIME_PLUGIN
from .downloads import PLUGIN as DOWNLOADS_PLUGIN
from .history import PLUGIN as HISTORY_PLUGIN
from .mail import PLUGIN as MAIL_PLUGIN
from .mcp import PLUGIN as MCP_PLUGIN
from .memory import PLUGIN as MEMORY_PLUGIN
from .python import PLUGIN as PYTHON_PLUGIN
from .search import PLUGIN as SEARCH_PLUGIN
from .terminal import PLUGIN as TERMINAL_PLUGIN
from .webapp import PLUGIN as WEBAPP_PLUGIN
from .webdav import PLUGIN as WEBDAV_PLUGIN


_INTERNAL_PLUGINS: tuple[InternalPluginDescriptor, ...] = (
    TERMINAL_PLUGIN,
    HISTORY_PLUGIN,
    DATETIME_PLUGIN,
    MEMORY_PLUGIN,
    MAIL_PLUGIN,
    CALENDAR_PLUGIN,
    SEARCH_PLUGIN,
    BROWSER_PLUGIN,
    DOWNLOADS_PLUGIN,
    WEBDAV_PLUGIN,
    WEBAPP_PLUGIN,
    PYTHON_PLUGIN,
    MCP_PLUGIN,
    API_PLUGIN,
)

_PLUGIN_BY_ID = {plugin.plugin_id: plugin for plugin in _INTERNAL_PLUGINS}
_PLUGIN_BY_SUBTYPE = {
    subtype: plugin
    for plugin in _INTERNAL_PLUGINS
    for subtype in plugin.builtin_subtypes
}
_PLUGIN_BY_PYTHON_PATH = {
    path: plugin
    for plugin in _INTERNAL_PLUGINS
    for path in ((plugin.python_path,) + plugin.legacy_python_paths)
    if path
}


def get_internal_plugins() -> tuple[InternalPluginDescriptor, ...]:
    return _INTERNAL_PLUGINS


def get_plugin(plugin_id: str) -> InternalPluginDescriptor | None:
    return _PLUGIN_BY_ID.get(str(plugin_id or "").strip())


def get_plugin_for_builtin_subtype(subtype: str) -> InternalPluginDescriptor | None:
    return _PLUGIN_BY_SUBTYPE.get(str(subtype or "").strip())


def get_plugin_for_builtin_python_path(python_path: str) -> InternalPluginDescriptor | None:
    return _PLUGIN_BY_PYTHON_PATH.get(str(python_path or "").strip())


def get_builtin_plugin_metadata(subtype: str) -> dict[str, Any] | None:
    plugin = get_plugin_for_builtin_subtype(subtype)
    if plugin is None:
        return None
    return plugin.build_builtin_metadata()


def resolve_active_plugins(
    *,
    tools: list[Any],
    subagents: list[Any],
) -> dict[str, ResolvedInternalPlugin]:
    resolved: "OrderedDict[str, ResolvedInternalPlugin]" = OrderedDict()
    for plugin in _INTERNAL_PLUGINS:
        if plugin.kind == "system" and plugin.runtime_capability_resolver is None:
            resolved[plugin.plugin_id] = ResolvedInternalPlugin(descriptor=plugin)
            continue
        if plugin.runtime_capability_resolver is None:
            continue
        features = plugin.runtime_capability_resolver(tools, subagents)
        if features:
            resolved[plugin.plugin_id] = ResolvedInternalPlugin(
                descriptor=plugin,
                features=dict(features),
            )
    return resolved
