import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import xml.etree.ElementTree as ET

import aiohttp
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool, ToolCredential

logger = logging.getLogger(__name__)

WEBDAV_NS = {"d": "DAV:"}


METADATA = {
    "name": "WebDAV Files",
    "description": "Browse and manipulate files in any WebDAV-compatible server (e.g., Nextcloud).",
    "loading": {
        "mode": "skill",
        "skill_id": "webdav",
        "skill_label": "WebDAV",
    },
    "requires_config": True,
    "config_fields": [
        {"name": "server_url", "type": "string", "label": _("WebDAV Server URL"), "required": True},
        {"name": "username", "type": "string", "label": _("WebDAV Username"), "required": True},
        {"name": "app_password", "type": "password", "label": _("WebDAV Password / App Password"), "required": True},
        {"name": "root_path", "type": "string", "label": _("Root path (optional)"), "required": False},
        {"name": "timeout", "type": "integer", "label": _("HTTP Timeout (seconds)"), "required": False, "default": 20},
        {"name": "allow_move", "type": "boolean", "label": _("Allow moving/renaming files and directories"), "required": False, "default": False},
        {"name": "allow_copy", "type": "boolean", "label": _("Allow copying files and directories"), "required": False, "default": False},
        {"name": "allow_batch_move", "type": "boolean", "label": _("Allow batch move planning/execution"), "required": False, "default": False},
        {"name": "allow_create_files", "type": "boolean", "label": _("Allow creating/updating files"), "required": False, "default": False},
        {"name": "allow_create_directories", "type": "boolean", "label": _("Allow creating directories"), "required": False, "default": False},
        {"name": "allow_delete", "type": "boolean", "label": _("Allow deleting files and directories"), "required": False, "default": False},
    ],
    "test_function": "test_webdav_access",
    "test_function_args": ["tool"],
}


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    del agent, tools
    return [
        "Start with webdav_stat_path or webdav_list_files before read/write/move/copy/delete actions.",
        "Use webdav_batch_move_paths with dry_run=true first for large reorganization tasks.",
        "Configure root_path and mutation permissions narrowly to reduce accidental destructive actions.",
    ]


def _normalize_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return "/"
    raw = "/" + raw.lstrip("/")
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw


def _join_paths(base: str, path: str) -> str:
    base = _normalize_path(base)
    if base == "/":
        return _normalize_path(path)
    return _normalize_path(f"{base}/{path.lstrip('/')}")


def _build_webdav_url(server_url: str, full_path: str) -> str:
    server = server_url.rstrip("/")
    safe_segments = [quote(part, safe="") for part in full_path.split("/") if part]
    rel = "/".join(safe_segments)
    if rel:
        return f"{server}/{rel}"
    return server


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


async def _get_webdav_config(tool: Tool) -> Dict[str, Any]:
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    cred = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False,
    )()
    if not cred:
        raise ValueError(_("No credential configured for this WebDAV tool."))

    server_url = (cred.config.get("server_url") or "").strip()
    username = (cred.config.get("username") or "").strip()
    password = (cred.config.get("app_password") or "").strip()
    root_path = _normalize_path(cred.config.get("root_path") or "/")
    timeout = int(cred.config.get("timeout") or 20)

    if not server_url or not username or not password:
        raise ValueError(_("Missing required WebDAV config: server_url, username or app_password."))

    return {
        "server_url": server_url,
        "username": username,
        "password": password,
        "root_path": root_path,
        "timeout": timeout,
        "allow_move": _coerce_bool(cred.config.get("allow_move"), default=False),
        "allow_copy": _coerce_bool(cred.config.get("allow_copy"), default=False),
        "allow_batch_move": _coerce_bool(cred.config.get("allow_batch_move"), default=False),
        "allow_create_files": _coerce_bool(cred.config.get("allow_create_files"), default=False),
        "allow_create_directories": _coerce_bool(cred.config.get("allow_create_directories"), default=False),
        "allow_delete": _coerce_bool(cred.config.get("allow_delete"), default=False),
    }


async def _webdav_request(
    config: Dict[str, Any],
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str | bytes] = None,
    expected_statuses: Optional[set[int]] = None,
) -> tuple[int, str]:
    full_path = _join_paths(config["root_path"], path)
    url = _build_webdav_url(config["server_url"], full_path)

    req_headers = dict(headers or {})
    auth = aiohttp.BasicAuth(config["username"], config["password"])
    timeout = aiohttp.ClientTimeout(total=config["timeout"])

    async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
        async with session.request(method, url, headers=req_headers, data=data) as response:
            text = await response.text()
            allowed = expected_statuses or {200, 201, 204, 207}
            if response.status not in allowed:
                raise ValueError(
                    _("WebDAV request failed ({status}): {body}").format(
                        status=response.status,
                        body=text[:500],
                    )
                )
            return response.status, text


async def test_webdav_access(tool: Tool) -> Dict[str, str]:
    config = await _get_webdav_config(tool)
    await _webdav_request(
        config,
        "PROPFIND",
        "/",
        headers={"Depth": "0"},
        data="""<?xml version=\"1.0\"?><d:propfind xmlns:d=\"DAV:\"><d:prop><d:displayname/></d:prop></d:propfind>""",
        expected_statuses={207},
    )
    return {"status": "success", "message": _("WebDAV connection successful")}


async def list_files(tool: Tool, path: str = "/", depth: int = 1) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    query = """<?xml version=\"1.0\"?><d:propfind xmlns:d=\"DAV:\"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>"""
    _, body = await _webdav_request(
        config,
        "PROPFIND",
        path,
        headers={"Depth": str(max(0, min(depth, 2)))},
        data=query,
        expected_statuses={207},
    )

    root = ET.fromstring(body)
    responses = root.findall("d:response", WEBDAV_NS)
    items: List[Dict[str, Any]] = []
    for response in responses:
        href = response.findtext("d:href", default="", namespaces=WEBDAV_NS)
        resource_type = response.find("d:propstat/d:prop/d:resourcetype", WEBDAV_NS)
        is_dir = resource_type is not None and resource_type.find("d:collection", WEBDAV_NS) is not None
        content_length = response.findtext("d:propstat/d:prop/d:getcontentlength", default="", namespaces=WEBDAV_NS)
        modified = response.findtext("d:propstat/d:prop/d:getlastmodified", default="", namespaces=WEBDAV_NS)
        items.append(
            {
                "href": href,
                "type": "directory" if is_dir else "file",
                "size": int(content_length) if content_length.isdigit() else None,
                "modified": modified or None,
            }
        )

    return {"items": items}


async def stat_path(tool: Tool, path: str) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    query = """<?xml version=\"1.0\"?><d:propfind xmlns:d=\"DAV:\"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>"""
    _, body = await _webdav_request(
        config,
        "PROPFIND",
        path,
        headers={"Depth": "0"},
        data=query,
        expected_statuses={207},
    )

    root = ET.fromstring(body)
    response = root.find("d:response", WEBDAV_NS)
    if response is None:
        return {"exists": False}

    href = response.findtext("d:href", default="", namespaces=WEBDAV_NS)
    resource_type = response.find("d:propstat/d:prop/d:resourcetype", WEBDAV_NS)
    is_dir = resource_type is not None and resource_type.find("d:collection", WEBDAV_NS) is not None
    content_length = response.findtext("d:propstat/d:prop/d:getcontentlength", default="", namespaces=WEBDAV_NS)
    modified = response.findtext("d:propstat/d:prop/d:getlastmodified", default="", namespaces=WEBDAV_NS)

    return {
        "exists": True,
        "href": href,
        "type": "directory" if is_dir else "file",
        "size": int(content_length) if content_length.isdigit() else None,
        "modified": modified or None,
    }


async def read_file(tool: Tool, path: str) -> str:
    config = await _get_webdav_config(tool)
    _, body = await _webdav_request(config, "GET", path, expected_statuses={200})
    return body


async def write_file(tool: Tool, path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    headers = {"Overwrite": "T" if overwrite else "F"}
    status, _ = await _webdav_request(
        config,
        "PUT",
        path,
        headers=headers,
        data=content.encode("utf-8"),
        expected_statuses={201, 204},
    )
    return {"status": "ok", "http_status": status}


async def create_folder(tool: Tool, path: str, recursive: bool = False) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)

    if not recursive:
        status, _ = await _webdav_request(config, "MKCOL", path, expected_statuses={201, 405})
        return {"status": "ok", "http_status": status}

    normalized = _normalize_path(path)
    segments = [segment for segment in normalized.split("/") if segment]
    statuses: List[Dict[str, Any]] = []
    current = "/"
    for segment in segments:
        current = _join_paths(current, segment)
        status, _ = await _webdav_request(config, "MKCOL", current, expected_statuses={201, 405})
        statuses.append({"path": current, "http_status": status})

    return {"status": "ok", "created": statuses}


async def delete_path(tool: Tool, path: str) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    status, _ = await _webdav_request(config, "DELETE", path, expected_statuses={204})
    return {"status": "ok", "http_status": status}


async def move_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    destination_full = _join_paths(config["root_path"], destination_path)
    destination_url = _build_webdav_url(config["server_url"], destination_full)

    status, _ = await _webdav_request(
        config,
        "MOVE",
        source_path,
        headers={
            "Destination": destination_url,
            "Overwrite": "T" if overwrite else "F",
        },
        expected_statuses={201, 204},
    )
    return {"status": "ok", "http_status": status}


async def copy_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
    config = await _get_webdav_config(tool)
    destination_full = _join_paths(config["root_path"], destination_path)
    destination_url = _build_webdav_url(config["server_url"], destination_full)

    status, _ = await _webdav_request(
        config,
        "COPY",
        source_path,
        headers={
            "Destination": destination_url,
            "Overwrite": "T" if overwrite else "F",
        },
        expected_statuses={201, 204},
    )
    return {"status": "ok", "http_status": status}


async def batch_move_paths(
    tool: Tool,
    operations: List[Dict[str, str]],
    dry_run: bool = True,
    overwrite: bool = False,
) -> Dict[str, Any]:
    plan = []
    for op in operations or []:
        source_path = _normalize_path((op or {}).get("source_path", ""))
        destination_path = _normalize_path((op or {}).get("destination_path", ""))
        if source_path == "/" or destination_path == "/":
            continue
        plan.append({"source_path": source_path, "destination_path": destination_path})

    if dry_run:
        return {"status": "dry_run", "planned_count": len(plan), "operations": plan}

    applied = []
    errors = []
    for op in plan:
        try:
            result = await move_path(
                tool,
                source_path=op["source_path"],
                destination_path=op["destination_path"],
                overwrite=overwrite,
            )
            applied.append({**op, **result})
        except Exception as error:
            errors.append({**op, "error": str(error)})

    return {
        "status": "ok" if not errors else "partial",
        "applied_count": len(applied),
        "error_count": len(errors),
        "applied": applied,
        "errors": errors,
    }


async def get_functions(tool: Tool, agent: LLMAgent) -> List[StructuredTool]:
    del agent

    config = await _get_webdav_config(tool)

    async def _list(path: str = "/", depth: int = 1) -> Dict[str, Any]:
        return await list_files(tool, path=path, depth=depth)

    async def _stat(path: str) -> Dict[str, Any]:
        return await stat_path(tool, path=path)

    async def _read(path: str) -> str:
        return await read_file(tool, path=path)

    async def _write(path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
        return await write_file(tool, path=path, content=content, overwrite=overwrite)

    async def _mkdir(path: str, recursive: bool = False) -> Dict[str, Any]:
        return await create_folder(tool, path=path, recursive=recursive)

    async def _delete(path: str) -> Dict[str, Any]:
        return await delete_path(tool, path=path)

    async def _move(source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
        return await move_path(tool, source_path=source_path, destination_path=destination_path, overwrite=overwrite)

    async def _copy(source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
        return await copy_path(tool, source_path=source_path, destination_path=destination_path, overwrite=overwrite)

    async def _batch_move(operations: List[Dict[str, str]], dry_run: bool = True, overwrite: bool = False) -> Dict[str, Any]:
        return await batch_move_paths(tool, operations=operations, dry_run=dry_run, overwrite=overwrite)

    tools = [
        StructuredTool.from_function(
            coroutine=_list,
            name="webdav_list_files",
            description="List files/folders in a WebDAV path.",
        ),
        StructuredTool.from_function(
            coroutine=_stat,
            name="webdav_stat_path",
            description="Get metadata/existence for a single path in WebDAV.",
        ),
        StructuredTool.from_function(
            coroutine=_read,
            name="webdav_read_file",
            description="Read a text file from WebDAV.",
        ),
    ]

    if config.get("allow_create_files"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_write,
                name="webdav_write_file",
                description="Create/update text content in a WebDAV file.",
            )
        )

    if config.get("allow_create_directories"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_mkdir,
                name="webdav_create_folder",
                description="Create a folder in WebDAV (supports recursive mode).",
            )
        )

    if config.get("allow_move"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_move,
                name="webdav_move_path",
                description="Move or rename a file/folder in WebDAV.",
            )
        )

    if config.get("allow_copy"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_copy,
                name="webdav_copy_path",
                description="Copy a file/folder in WebDAV.",
            )
        )

    if config.get("allow_batch_move"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_batch_move,
                name="webdav_batch_move_paths",
                description="Plan/execute multiple move operations in one call.",
            )
        )

    if config.get("allow_delete"):
        tools.append(
            StructuredTool.from_function(
                coroutine=_delete,
                name="webdav_delete_path",
                description="Delete a file or folder in WebDAV.",
            )
        )

    return tools
