from __future__ import annotations

import importlib
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable


RuntimeCapabilityResolver = Callable[[list[Any], list[Any]], dict[str, Any] | None]
SkillDocsProvider = Callable[[Any, str | None], dict[str, str]]
ConnectionTestHandler = Callable[..., Any] | str


@dataclass(frozen=True, slots=True)
class InternalPluginDescriptor:
    plugin_id: str
    label: str
    kind: str
    builtin_subtypes: tuple[str, ...] = ()
    tool_types: tuple[str, ...] = ()
    command_families: tuple[str, ...] = ()
    settings_metadata: dict[str, Any] | None = None
    runtime_capability_resolver: RuntimeCapabilityResolver | None = None
    skill_docs_provider: SkillDocsProvider | None = None
    test_connection_handler: ConnectionTestHandler | None = None
    python_path: str = ""
    legacy_python_paths: tuple[str, ...] = ()
    catalog_section: str = "connections"
    selection_mode: str = "multi_instance"
    provisioning_sources: tuple[str, ...] = ()
    show_in_add_flow: bool = False
    add_label: str = ""
    default_enabled_for_primary_agents: bool = False

    def build_builtin_metadata(self) -> dict[str, Any]:
        metadata = deepcopy(self.settings_metadata or {})
        metadata.setdefault("name", self.label)
        metadata.setdefault("description", self.label)
        metadata["plugin_id"] = self.plugin_id
        metadata["python_path"] = self.python_path
        metadata["catalog_section"] = self.catalog_section
        metadata["selection_mode"] = self.selection_mode
        metadata["provisioning_sources"] = list(self.provisioning_sources)
        metadata["show_in_add_flow"] = self.show_in_add_flow
        metadata["add_label"] = self.add_label or self.label
        metadata["default_enabled_for_primary_agents"] = (
            self.default_enabled_for_primary_agents
        )
        if self.test_connection_handler is not None:
            metadata["test_connection_handler"] = _resolve_connection_test_handler(
                self.test_connection_handler
            )
        return metadata

    def build_skill_docs(self, capabilities: Any, *, thread_mode: str | None = None) -> dict[str, str]:
        if self.skill_docs_provider is None:
            return {}
        return dict(self.skill_docs_provider(capabilities, thread_mode))


@dataclass(frozen=True, slots=True)
class ResolvedInternalPlugin:
    descriptor: InternalPluginDescriptor
    features: dict[str, Any] = field(default_factory=dict)


def _resolve_connection_test_handler(handler: ConnectionTestHandler) -> Callable[..., Any]:
    if callable(handler):
        return handler
    module_path, _, attribute = str(handler).rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, attribute)


def resolve_single_builtin_tool(subtype: str) -> RuntimeCapabilityResolver:
    def _resolver(tools: list[Any], _subagents: list[Any]) -> dict[str, Any] | None:
        tool = next((item for item in tools if getattr(item, "tool_subtype", "") == subtype), None)
        if tool is None:
            return None
        return {"tool": tool}

    return _resolver


def resolve_multi_builtin_tools(subtype: str) -> RuntimeCapabilityResolver:
    def _resolver(tools: list[Any], _subagents: list[Any]) -> dict[str, Any] | None:
        matched = [item for item in tools if getattr(item, "tool_subtype", "") == subtype]
        if not matched:
            return None
        return {"tools": matched}

    return _resolver


def resolve_external_tool_type(tool_type: str) -> RuntimeCapabilityResolver:
    def _resolver(tools: list[Any], _subagents: list[Any]) -> dict[str, Any] | None:
        matched = [item for item in tools if getattr(item, "tool_type", "") == tool_type]
        if not matched:
            return None
        return {"tools": matched}

    return _resolver


def resolve_downloads_from_browser(tools: list[Any], _subagents: list[Any]) -> dict[str, Any] | None:
    tool = next((item for item in tools if getattr(item, "tool_subtype", "") == "browser"), None)
    if tool is None:
        return None
    return {"tool": tool}
