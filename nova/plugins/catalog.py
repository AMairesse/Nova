from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from nova.llm.embeddings import resolve_embeddings_provider_for_values
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters
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
    allow_custom_backends: bool


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
        return "System - Python"
    plugin = get_plugin("search" if subtype == "searxng" else "python")
    label = plugin.label if plugin is not None else subtype
    return f"Local Nova {label}"


def _builtin_python_path(subtype: str) -> str:
    metadata = _plugin_metadata_for_subtype(subtype)
    return str(metadata.get("python_path") or "").strip()


def _prefetched_tool_credentials(tool: Tool) -> list[ToolCredential]:
    prefetched = getattr(tool, "_prefetched_objects_cache", {})
    credentials = prefetched.get("credentials")
    if credentials is not None:
        return list(credentials)
    return list(tool.credentials.all())


def _tool_credential_for_status(tool: Tool) -> ToolCredential | None:
    credentials = _prefetched_tool_credentials(tool)
    if tool.user_id is None:
        return next((credential for credential in credentials if credential.user_id is None), None)
    return next((credential for credential in credentials if credential.user_id == tool.user_id), None)


def _status_payload(key: str, label: str, badge_class: str) -> dict[str, str]:
    return {
        "key": key,
        "label": label,
        "badge_class": badge_class,
    }


def _config_field_is_visible(field_definition: dict[str, Any], values: dict[str, Any]) -> bool:
    visible_if = field_definition.get("visible_if")
    if not isinstance(visible_if, dict):
        return True
    field_name = str(visible_if.get("field") or "").strip()
    if not field_name or "equals" not in visible_if:
        return True
    return values.get(field_name) == visible_if.get("equals")


def _builtin_connection_status(tool: Tool) -> dict[str, str]:
    plugin = get_plugin_for_builtin_subtype(tool.tool_subtype or "")
    metadata = plugin.build_builtin_metadata() if plugin is not None else {}
    config_fields = list(metadata.get("config_fields") or [])
    if not config_fields:
        return _status_payload("ready", "Ready", "bg-success-subtle text-success-emphasis")

    credential = _tool_credential_for_status(tool)
    if credential is None:
        return _status_payload("needs_setup", "Needs setup", "bg-warning-subtle text-warning-emphasis")

    values = dict(credential.config or {})
    for field_definition in config_fields:
        if not field_definition.get("required"):
            continue
        if not _config_field_is_visible(field_definition, values):
            continue
        field_name = str(field_definition.get("name") or "").strip()
        if not field_name:
            continue
        field_value = values.get(field_name)
        if field_value in (None, ""):
            return _status_payload("needs_setup", "Needs setup", "bg-warning-subtle text-warning-emphasis")
    return _status_payload("ready", "Ready", "bg-success-subtle text-success-emphasis")


def _manual_connection_status(tool: Tool) -> dict[str, str]:
    credential = _tool_credential_for_status(tool)
    if credential is None:
        return _status_payload("needs_setup", "Needs setup", "bg-warning-subtle text-warning-emphasis")

    auth_type = str(credential.auth_type or "").strip().lower() or "none"
    if auth_type == "oauth_managed" and tool.tool_type == Tool.ToolType.MCP:
        oauth_config = {}
        if isinstance(credential.config, dict):
            oauth_config = credential.config.get("mcp_oauth") or {}
        if not isinstance(oauth_config, dict):
            oauth_config = {}
        status = str(oauth_config.get("status") or "").strip().lower()
        if status == "connected":
            return _status_payload("connected", "Connected", "bg-success-subtle text-success-emphasis")
        if status == "reconnect_required":
            return _status_payload(
                "reconnect_required",
                "Reconnect required",
                "bg-warning-subtle text-warning-emphasis",
            )
        return _status_payload("not_connected", "Not connected", "bg-secondary-subtle text-secondary-emphasis")

    if auth_type == "none":
        return _status_payload("ready", "Ready", "bg-success-subtle text-success-emphasis")
    if auth_type == "basic":
        ready = bool(credential.username and credential.password)
    elif auth_type == "token":
        ready = bool(credential.token)
    elif auth_type == "api_key":
        api_key_name = ""
        api_key_in = ""
        if isinstance(credential.config, dict):
            api_key_name = str(credential.config.get("api_key_name") or "").strip()
            api_key_in = str(credential.config.get("api_key_in") or "").strip().lower()
        ready = bool(credential.token and api_key_name and api_key_in in {"header", "query"})
    else:
        ready = False
    if ready:
        return _status_payload("ready", "Ready", "bg-success-subtle text-success-emphasis")
    return _status_payload("needs_setup", "Needs setup", "bg-warning-subtle text-warning-emphasis")


def get_tool_connection_status(tool: Tool) -> dict[str, str]:
    if tool.tool_type == Tool.ToolType.BUILTIN:
        return _builtin_connection_status(tool)
    return _manual_connection_status(tool)


def get_tool_connection_type_label(tool: Tool) -> str:
    if tool.tool_type == Tool.ToolType.BUILTIN:
        plugin = get_plugin_for_builtin_subtype(tool.tool_subtype or "")
        if plugin is not None:
            return plugin.label
    return tool.get_tool_type_display()


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
    tool = _get_system_builtin_tool("code_execution")

    if settings.EXEC_RUNNER_ENABLED:
        if tool is None:
            tool = Tool.objects.create(
                user=None,
                name=_default_backend_name("code_execution"),
                description="Default local Python capability managed by Nova.",
                tool_type=Tool.ToolType.BUILTIN,
                tool_subtype="code_execution",
                python_path=_builtin_python_path("code_execution"),
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
    return Tool.objects.filter(Q(user=user) | Q(user__isnull=True)).prefetch_related(
        "credentials",
    ).annotate(
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


def _resolve_builtin_settings_action(plugin) -> dict[str, str] | None:
    if plugin is None:
        return None
    metadata = plugin.build_builtin_metadata()
    route_name = str(metadata.get("settings_route_name") or "").strip()
    if not route_name:
        return None
    url = reverse(route_name)
    anchor = str(metadata.get("settings_anchor") or "").strip()
    if anchor:
        url = f"{url}{anchor}"
    label = str(metadata.get("settings_label") or _("Open settings")).strip()
    return {
        "url": url,
        "label": label,
    }


def _memory_status_summary_for_user(user) -> dict[str, str]:
    params = UserParameters.objects.filter(user=user).first()
    source = MemoryEmbeddingsSource.SYSTEM
    base_url = ""
    model = ""
    api_key = None
    if params is not None:
        source = str(params.memory_embeddings_source or MemoryEmbeddingsSource.SYSTEM)
        base_url = (params.memory_embeddings_url or "").strip()
        model = (params.memory_embeddings_model or "").strip()
        api_key = params.memory_embeddings_api_key or None

    resolved = resolve_embeddings_provider_for_values(
        selected_source=source,
        base_url=base_url,
        model=model,
        api_key=api_key,
        sync_system_state=False,
    )

    if resolved.provider is not None:
        return {
            "label": _("Semantic retrieval"),
            "value": _("Semantic search ready"),
            "badge_class": "bg-success-subtle text-success-emphasis",
        }
    if resolved.selected_source == MemoryEmbeddingsSource.DISABLED:
        return {
            "label": _("Semantic retrieval"),
            "value": _("Embeddings disabled"),
            "badge_class": "bg-secondary-subtle text-secondary-emphasis",
        }
    return {
        "label": _("Semantic retrieval"),
        "value": _("Lexical only"),
        "badge_class": "bg-warning-subtle text-warning-emphasis",
    }


def _builtin_status_summary_for_user(user, subtype: str) -> dict[str, str] | None:
    if subtype == "memory":
        return _memory_status_summary_for_user(user)
    return None


def build_agent_tool_selection_catalog(user, *, include_selected_ids: set[int] | None = None) -> dict:
    ensure_capability_tooling()
    include_selected_ids = include_selected_ids or set()

    standard_tools = list(
        Tool.objects.filter(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype__in=STANDARD_CAPABILITY_SUBTYPES,
        ).order_by("id")
    )

    search_backends = list(
        Tool.objects.filter(
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
        )
        .filter(Q(user=user) | Q(user__isnull=True))
        .order_by("user_id", "name", "id")
    )
    python_backends = list(
        Tool.objects.filter(
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
        )
        .filter(Q(user=user) | Q(user__isnull=True))
        .order_by("user_id", "name", "id")
    )
    connection_tools = Tool.objects.filter(
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
        tool.connection_status = get_tool_connection_status(tool)
        tool.connection_type_label = get_tool_connection_type_label(tool)
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
                "settings_action": _resolve_builtin_settings_action(plugin),
                "status_summary": _builtin_status_summary_for_user(user, subtype),
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
                allow_custom_backends=bool(plugin.show_in_add_flow) if plugin is not None else False,
            )
        )

    return {
        "built_in_capabilities": built_ins,
        "backend_families": backend_families,
        "connections": connections,
        "has_connections": bool(connections),
    }
