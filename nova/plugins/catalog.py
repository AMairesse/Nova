from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count, Q

from nova.models.Tool import Tool, ToolCredential
from nova.plugins import get_internal_plugins, get_plugin, get_plugin_for_builtin_subtype


STANDARD_CAPABILITY_SUBTYPES = ("date", "memory", "browser", "webapp")
BACKEND_CAPABILITY_SUBTYPES = ("searxng", "code_execution")
MULTI_INSTANCE_SUBTYPES = ("email", "caldav", "webdav")


@dataclass(frozen=True, slots=True)
class CatalogBackendFamily:
    plugin_id: str
    label: str
    description: str
    subtype: str
    default_backend: Tool | None
    custom_backends: list[Tool]


def _plugin_metadata_for_subtype(subtype: str) -> dict:
    plugin = get_plugin_for_builtin_subtype(subtype)
    if plugin is None:
        return {}
    return plugin.build_builtin_metadata()


def _system_tool_defaults(subtype: str) -> tuple[str, str]:
    metadata = _plugin_metadata_for_subtype(subtype)
    return (
        str(metadata.get("name") or subtype).strip(),
        str(metadata.get("description") or metadata.get("name") or subtype).strip(),
    )


def _default_backend_name(subtype: str) -> str:
    if subtype == "searxng":
        return "System - SearXNG"
    if subtype == "code_execution":
        return "System - Code Execution"
    plugin = get_plugin("search" if subtype == "searxng" else "python")
    label = plugin.label if plugin is not None else subtype
    return f"Local Nova {label}"


def _builtin_python_path(subtype: str) -> str:
    metadata = _plugin_metadata_for_subtype(subtype)
    return str(metadata.get("python_path") or "").strip()


def _get_system_builtin_tool(subtype: str) -> Tool | None:
    return (
        Tool.objects.filter(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
        )
        .order_by("id")
        .first()
    )


def _sync_default_credential(tool: Tool, *, config: dict) -> None:
    credential, _ = ToolCredential.objects.get_or_create(
        user=None,
        tool=tool,
        defaults={"auth_type": "none"},
    )
    if credential.config != config:
        credential.config = dict(config)
        credential.save(update_fields=["config", "updated_at"])


def ensure_standard_capability_tools() -> dict[str, Tool]:
    ensured: dict[str, Tool] = {}
    for subtype in STANDARD_CAPABILITY_SUBTYPES:
        tool = _get_system_builtin_tool(subtype)
        name, description = _system_tool_defaults(subtype)
        if tool is None:
            tool = Tool.objects.create(
                user=None,
                name=name,
                description=description,
                tool_type=Tool.ToolType.BUILTIN,
                tool_subtype=subtype,
                python_path=_builtin_python_path(subtype),
            )
        ensured[subtype] = tool
    return ensured


def sync_search_system_backend() -> Tool | None:
    searxng_url = settings.SEARNGX_SERVER_URL
    num_results = settings.SEARNGX_NUM_RESULTS
    tool = _get_system_builtin_tool("searxng")

    if searxng_url and num_results:
        if tool is None:
            tool = Tool.objects.create(
                user=None,
                name=_default_backend_name("searxng"),
                description="Default local SearXNG backend managed by Nova.",
                tool_type=Tool.ToolType.BUILTIN,
                tool_subtype="searxng",
                python_path=_builtin_python_path("searxng"),
            )
        _sync_default_credential(
            tool,
            config={
                "searxng_url": searxng_url,
                "num_results": num_results,
            },
        )
        return tool

    if tool is not None and not tool.agents.exists():
        tool.delete()
        return None
    return tool


def sync_python_system_backend() -> Tool | None:
    judge0_url = settings.JUDGE0_SERVER_URL
    tool = _get_system_builtin_tool("code_execution")

    if judge0_url:
        if tool is None:
            tool = Tool.objects.create(
                user=None,
                name=_default_backend_name("code_execution"),
                description="Default local Judge0 backend managed by Nova.",
                tool_type=Tool.ToolType.BUILTIN,
                tool_subtype="code_execution",
                python_path=_builtin_python_path("code_execution"),
            )
        _sync_default_credential(
            tool,
            config={"judge0_url": judge0_url},
        )
        return tool

    if tool is not None and not tool.agents.exists():
        tool.delete()
        return None
    return tool


def ensure_capability_tooling() -> None:
    ensure_standard_capability_tools()
    sync_search_system_backend()
    sync_python_system_backend()


def get_accessible_tools_queryset(user):
    return Tool.objects.filter(Q(user=user) | Q(user__isnull=True)).annotate(
        agent_count=Count(
            "agents",
            filter=Q(agents__user=user),
            distinct=True,
        )
    )


def get_user_creatable_plugins():
    return tuple(
        plugin
        for plugin in get_internal_plugins()
        if plugin.show_in_add_flow
    )


def get_user_creatable_connection_choices() -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for plugin in get_user_creatable_plugins():
        choices.append((plugin.plugin_id, plugin.add_label or plugin.label))
    return choices


def resolve_connection_kind(kind: str):
    plugin = get_plugin(str(kind or "").strip())
    if plugin is None or not plugin.show_in_add_flow:
        return None
    if plugin.builtin_subtypes:
        return {
            "plugin": plugin,
            "tool_type": Tool.ToolType.BUILTIN,
            "tool_subtype": plugin.builtin_subtypes[0],
        }
    if plugin.tool_types:
        return {
            "plugin": plugin,
            "tool_type": plugin.tool_types[0],
            "tool_subtype": "",
        }
    return None


def get_preferred_backend_tool(user, subtype: str) -> Tool | None:
    ensure_capability_tooling()

    system_tool = (
        Tool.objects.filter(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
            is_active=True,
        )
        .order_by("id")
        .first()
    )
    if system_tool is not None:
        return system_tool

    custom_tools = list(
        Tool.objects.filter(
            user=user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
            is_active=True,
        ).order_by("name", "id")
    )
    if len(custom_tools) == 1:
        return custom_tools[0]
    return None


def get_standard_capability_tools():
    ensure_capability_tooling()
    tools = []
    for subtype in STANDARD_CAPABILITY_SUBTYPES:
        tool = _get_system_builtin_tool(subtype)
        if tool is not None:
            tools.append(tool)
    return tools


def build_agent_tool_selection_catalog(user, *, include_selected_ids: set[int] | None = None) -> dict:
    ensure_capability_tooling()
    include_selected_ids = include_selected_ids or set()

    def _active_or_selected():
        return Q(is_active=True) | Q(pk__in=include_selected_ids)

    standard_tools = list(
        Tool.objects.filter(
            _active_or_selected(),
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype__in=STANDARD_CAPABILITY_SUBTYPES,
        ).order_by("id")
    )

    search_backends = list(
        Tool.objects.filter(
            _active_or_selected(),
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
        )
        .filter(Q(user=user) | Q(user__isnull=True))
        .order_by("user_id", "name", "id")
    )
    python_backends = list(
        Tool.objects.filter(
            _active_or_selected(),
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
        )
        .filter(Q(user=user) | Q(user__isnull=True))
        .order_by("user_id", "name", "id")
    )
    connection_tools = Tool.objects.filter(
        _active_or_selected(),
    ).filter(
        Q(user=user) | Q(user__isnull=True)
    ).filter(
        Q(tool_subtype__in=MULTI_INSTANCE_SUBTYPES)
        | Q(tool_type__in=(Tool.ToolType.API, Tool.ToolType.MCP))
    ).order_by("tool_type", "tool_subtype", "name", "id")

    return {
        "standard_tools": standard_tools,
        "search_backends": search_backends,
        "python_backends": python_backends,
        "connection_tools": connection_tools,
    }


def build_tools_page_catalog(user, *, tools: list[Tool] | None = None) -> dict:
    annotated_tools = list(tools or get_accessible_tools_queryset(user).order_by("user", "name", "id"))
    tools_by_subtype: dict[str, list[Tool]] = {}
    connections: list[Tool] = []

    for tool in annotated_tools:
        if tool.tool_subtype:
            tools_by_subtype.setdefault(tool.tool_subtype, []).append(tool)
        if tool.tool_type in {Tool.ToolType.API, Tool.ToolType.MCP} or tool.tool_subtype in MULTI_INSTANCE_SUBTYPES:
            connections.append(tool)

    built_ins = []
    for subtype in STANDARD_CAPABILITY_SUBTYPES:
        plugin = get_plugin_for_builtin_subtype(subtype)
        system_tool = next((item for item in tools_by_subtype.get(subtype, []) if item.user_id is None), None)
        built_ins.append(
            {
                "plugin": plugin,
                "tool": system_tool,
                "label": plugin.label if plugin is not None else subtype,
                "description": (plugin.settings_metadata or {}).get("description", "") if plugin is not None else "",
                "agent_count": getattr(system_tool, "agent_count", 0) if system_tool is not None else 0,
            }
        )

    backend_families: list[CatalogBackendFamily] = []
    for plugin_id, subtype in (("search", "searxng"), ("python", "code_execution")):
        plugin = get_plugin(plugin_id)
        backend_families.append(
            CatalogBackendFamily(
                plugin_id=plugin_id,
                label=plugin.label if plugin is not None else plugin_id.title(),
                description=(plugin.settings_metadata or {}).get("description", "") if plugin is not None else "",
                subtype=subtype,
                default_backend=next(
                    (item for item in tools_by_subtype.get(subtype, []) if item.user_id is None),
                    None,
                ),
                custom_backends=[
                    item for item in tools_by_subtype.get(subtype, [])
                    if item.user_id == user.id
                ],
            )
        )

    return {
        "built_in_capabilities": built_ins,
        "backend_families": backend_families,
        "connections": connections,
        "has_connections": bool(connections),
    }
