from __future__ import annotations

import mimetypes
import posixpath
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

import aiohttp
from asgiref.sync import sync_to_async
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool, ToolCredential
from nova.web.network_policy import assert_allowed_egress_url

WEBDAV_NS = {"d": "DAV:"}
WEBDAV_VFS_ROOT = "/webdav"
WEBDAV_MAX_RECURSIVE_PATHS = 500


@dataclass(slots=True, frozen=True)
class WebDAVMount:
    name: str
    tool: Tool


def normalize_webdav_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return "/"
    raw = "/" + raw.lstrip("/")
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized or "/"


def join_webdav_paths(base: str, path: str) -> str:
    base_path = normalize_webdav_path(base)
    if base_path == "/":
        return normalize_webdav_path(path)
    return normalize_webdav_path(f"{base_path}/{str(path or '').lstrip('/')}")


def build_webdav_url(server_url: str, full_path: str) -> str:
    server = str(server_url or "").rstrip("/")
    safe_segments = [quote(unquote(part), safe="") for part in full_path.split("/") if part]
    relative = "/".join(safe_segments)
    if relative:
        return f"{server}/{relative}"
    return server


def coerce_bool(value: Any, default: bool = False) -> bool:
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


def build_webdav_mounts(tools: list[Tool]) -> list[WebDAVMount]:
    mounts: list[WebDAVMount] = []
    used_names: set[str] = set()
    for tool in list(tools or []):
        base = slugify(str(getattr(tool, "name", "") or "")) or f"webdav-{int(tool.id)}"
        candidate = base
        if candidate in used_names:
            candidate = f"{base}-{int(tool.id)}"
        while candidate in used_names:
            candidate = f"{candidate}-{int(tool.id)}"
        used_names.add(candidate)
        mounts.append(WebDAVMount(name=candidate, tool=tool))
    return mounts


async def get_webdav_config(tool: Tool) -> dict[str, Any]:
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    credential = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False,
    )()
    if not credential:
        raise ValueError(_("No credential configured for this WebDAV tool."))

    server_url = (credential.config.get("server_url") or "").strip()
    username = (credential.config.get("username") or "").strip()
    password = (credential.config.get("app_password") or "").strip()
    root_path = normalize_webdav_path(credential.config.get("root_path") or "/")
    timeout = int(credential.config.get("timeout") or 20)

    if not server_url or not username or not password:
        raise ValueError(_("Missing required WebDAV config: server_url, username or app_password."))

    return {
        "server_url": server_url,
        "username": username,
        "password": password,
        "root_path": root_path,
        "timeout": timeout,
        "allow_move": coerce_bool(credential.config.get("allow_move"), default=False),
        "allow_copy": coerce_bool(credential.config.get("allow_copy"), default=False),
        "allow_batch_move": coerce_bool(credential.config.get("allow_batch_move"), default=False),
        "allow_create_files": coerce_bool(credential.config.get("allow_create_files"), default=False),
        "allow_create_directories": coerce_bool(credential.config.get("allow_create_directories"), default=False),
        "allow_delete": coerce_bool(credential.config.get("allow_delete"), default=False),
    }


async def webdav_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    headers: Optional[dict[str, str]] = None,
    data: Optional[str | bytes] = None,
    expected_statuses: Optional[set[int]] = None,
) -> tuple[int, str]:
    full_path = join_webdav_paths(config["root_path"], path)
    url = build_webdav_url(config["server_url"], full_path)
    await assert_allowed_egress_url(url)

    request_headers = dict(headers or {})
    if request_headers.get("Destination"):
        await assert_allowed_egress_url(str(request_headers["Destination"]))
    auth = aiohttp.BasicAuth(config["username"], config["password"])
    timeout = aiohttp.ClientTimeout(total=config["timeout"])

    async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
        async with session.request(method, url, headers=request_headers, data=data, allow_redirects=False) as response:
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


async def webdav_request_binary(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    headers: Optional[dict[str, str]] = None,
    data: Optional[str | bytes] = None,
    expected_statuses: Optional[set[int]] = None,
) -> tuple[int, bytes, dict[str, str]]:
    full_path = join_webdav_paths(config["root_path"], path)
    url = build_webdav_url(config["server_url"], full_path)
    await assert_allowed_egress_url(url)

    request_headers = dict(headers or {})
    if request_headers.get("Destination"):
        await assert_allowed_egress_url(str(request_headers["Destination"]))
    auth = aiohttp.BasicAuth(config["username"], config["password"])
    timeout = aiohttp.ClientTimeout(total=config["timeout"])

    async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
        async with session.request(method, url, headers=request_headers, data=data, allow_redirects=False) as response:
            body = await response.read()
            allowed = expected_statuses or {200, 201, 204, 207}
            if response.status not in allowed:
                preview = body.decode("utf-8", errors="ignore")[:500]
                raise ValueError(
                    _("WebDAV request failed ({status}): {body}").format(
                        status=response.status,
                        body=preview,
                    )
                )
            return response.status, body, dict(response.headers)


def _server_mount_prefix(config: dict[str, Any]) -> str:
    server_path = normalize_webdav_path(urlparse(str(config["server_url"] or "")).path or "/")
    return join_webdav_paths(server_path, config["root_path"])


def _relative_from_prefix(href_path: str, prefix: str) -> str | None:
    normalized_prefix = normalize_webdav_path(prefix)
    if href_path == normalized_prefix:
        return "/"
    expected_prefix = normalized_prefix.rstrip("/") + "/"
    if href_path.startswith(expected_prefix):
        suffix = href_path[len(expected_prefix):]
        return normalize_webdav_path(f"/{suffix}")
    return None


def _href_to_mount_path(config: dict[str, Any], href: str) -> str | None:
    parsed = urlparse(str(href or ""))
    href_path = normalize_webdav_path(unquote(parsed.path or href or "/"))
    prefixes = [_server_mount_prefix(config)]
    root_path = normalize_webdav_path(config.get("root_path") or "/")
    if root_path != "/":
        prefixes.append(root_path)
    for prefix in prefixes:
        relative = _relative_from_prefix(href_path, prefix)
        if relative is not None:
            return relative
    if root_path != "/":
        marker_index = href_path.rfind(root_path)
        if marker_index >= 0:
            suffix = href_path[marker_index + len(root_path):]
            if not suffix:
                return "/"
            return normalize_webdav_path(f"/{suffix.lstrip('/')}")
    return None


async def _propfind_entries(tool: Tool, path: str, *, depth: int, allow_not_found: bool = False) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    config = await get_webdav_config(tool)
    query = """<?xml version=\"1.0\"?><d:propfind xmlns:d=\"DAV:\"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>"""
    expected = {207}
    if allow_not_found:
        expected.add(404)
    status, body = await webdav_request(
        config,
        "PROPFIND",
        path,
        headers={"Depth": str(max(0, min(depth, 1)))},
        data=query,
        expected_statuses=expected,
    )
    if status == 404:
        return status, config, []

    root = ET.fromstring(body)
    responses = root.findall("d:response", WEBDAV_NS)
    entries: list[dict[str, Any]] = []
    for response in responses:
        href = response.findtext("d:href", default="", namespaces=WEBDAV_NS)
        relative_path = _href_to_mount_path(config, href)
        if relative_path is None:
            continue
        resource_type = response.find("d:propstat/d:prop/d:resourcetype", WEBDAV_NS)
        is_dir = resource_type is not None and resource_type.find("d:collection", WEBDAV_NS) is not None
        content_length = response.findtext("d:propstat/d:prop/d:getcontentlength", default="", namespaces=WEBDAV_NS)
        modified = response.findtext("d:propstat/d:prop/d:getlastmodified", default="", namespaces=WEBDAV_NS)
        guessed_mime = None if is_dir else (mimetypes.guess_type(relative_path)[0] or "application/octet-stream")
        entries.append(
            {
                "href": href,
                "path": relative_path,
                "name": posixpath.basename(relative_path) if relative_path != "/" else "",
                "type": "directory" if is_dir else "file",
                "size": int(content_length) if content_length.isdigit() else None,
                "modified": modified or None,
                "mime_type": guessed_mime,
            }
        )
    return status, config, entries


async def list_files(tool: Tool, path: str = "/", depth: int = 1) -> dict[str, Any]:
    _status, _config, entries = await _propfind_entries(tool, path, depth=max(0, min(depth, 2)))
    return {
        "items": [
            {
                "href": entry["href"],
                "type": entry["type"],
                "size": entry["size"],
                "modified": entry["modified"],
            }
            for entry in entries
        ]
    }


async def list_directory(tool: Tool, path: str = "/") -> list[dict[str, Any]]:
    normalized = normalize_webdav_path(path)
    status, _config, entries = await _propfind_entries(tool, normalized, depth=1)
    if status == 404:
        raise ValueError(_("WebDAV path not found: {path}").format(path=normalized))
    filtered = [entry for entry in entries if entry["path"] != normalized]
    filtered.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    return filtered


async def stat_path(tool: Tool, path: str) -> dict[str, Any]:
    normalized = normalize_webdav_path(path)
    status, _config, entries = await _propfind_entries(tool, normalized, depth=0, allow_not_found=True)
    if status == 404 or not entries:
        return {"exists": False, "path": normalized}
    match = next((entry for entry in entries if entry["path"] == normalized), entries[0])
    return {
        "exists": True,
        "href": match["href"],
        "path": normalized,
        "type": match["type"],
        "size": match["size"],
        "modified": match["modified"],
        "mime_type": match["mime_type"],
    }


async def read_binary_file(tool: Tool, path: str) -> dict[str, Any]:
    normalized = normalize_webdav_path(path)
    config = await get_webdav_config(tool)
    _status, body, headers = await webdav_request_binary(
        config,
        "GET",
        normalized,
        expected_statuses={200},
    )
    mime_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if not mime_type:
        mime_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    return {
        "path": normalized,
        "content": body,
        "mime_type": mime_type,
        "size": len(body),
    }


async def read_text_file(tool: Tool, path: str) -> str:
    payload = await read_binary_file(tool, path)
    try:
        return payload["content"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Binary file cannot be displayed as text: {payload['path']} "
            f"({payload['mime_type']}, {payload['size']} bytes)"
        ) from exc


def _ensure_permission(config: dict[str, Any], key: str, message: str) -> None:
    if not config.get(key):
        raise ValueError(message)


async def write_bytes(
    tool: Tool,
    path: str,
    content: bytes,
    *,
    mime_type: str = "application/octet-stream",
    overwrite: bool = True,
) -> dict[str, Any]:
    normalized = normalize_webdav_path(path)
    config = await get_webdav_config(tool)
    _ensure_permission(config, "allow_create_files", "This WebDAV tool does not allow creating files.")
    headers = {
        "Overwrite": "T" if overwrite else "F",
        "Content-Type": mime_type or "application/octet-stream",
    }
    status, _body, response_headers = await webdav_request_binary(
        config,
        "PUT",
        normalized,
        headers=headers,
        data=bytes(content),
        expected_statuses={201, 204},
    )
    response_mime = str(response_headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    return {
        "status": "ok",
        "http_status": status,
        "path": normalized,
        "mime_type": response_mime or mime_type or "application/octet-stream",
        "size": len(content),
    }


async def write_text_file(tool: Tool, path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    return await write_bytes(
        tool,
        path,
        str(content or "").encode("utf-8"),
        mime_type="text/plain",
        overwrite=overwrite,
    )


async def create_folder(tool: Tool, path: str, recursive: bool = False) -> dict[str, Any]:
    config = await get_webdav_config(tool)
    _ensure_permission(config, "allow_create_directories", "This WebDAV tool does not allow creating directories.")

    if not recursive:
        status, _ = await webdav_request(config, "MKCOL", path, expected_statuses={201, 405})
        return {"status": "ok", "http_status": status}

    normalized = normalize_webdav_path(path)
    segments = [segment for segment in normalized.split("/") if segment]
    statuses: list[dict[str, Any]] = []
    current = "/"
    for segment in segments:
        current = join_webdav_paths(current, segment)
        status, _ = await webdav_request(config, "MKCOL", current, expected_statuses={201, 405})
        statuses.append({"path": current, "http_status": status})
    return {"status": "ok", "created": statuses}


async def delete_path(tool: Tool, path: str) -> dict[str, Any]:
    config = await get_webdav_config(tool)
    _ensure_permission(config, "allow_delete", "This WebDAV tool does not allow deleting paths.")
    normalized = normalize_webdav_path(path)
    status, _ = await webdav_request(config, "DELETE", normalized, expected_statuses={204})
    return {"status": "ok", "http_status": status, "path": normalized}


async def move_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, Any]:
    config = await get_webdav_config(tool)
    _ensure_permission(config, "allow_move", "This WebDAV tool does not allow moving or renaming paths.")
    normalized_source = normalize_webdav_path(source_path)
    normalized_destination = normalize_webdav_path(destination_path)
    destination_full = join_webdav_paths(config["root_path"], normalized_destination)
    destination_url = build_webdav_url(config["server_url"], destination_full)
    status, _ = await webdav_request(
        config,
        "MOVE",
        normalized_source,
        headers={
            "Destination": destination_url,
            "Overwrite": "T" if overwrite else "F",
        },
        expected_statuses={201, 204},
    )
    return {"status": "ok", "http_status": status, "path": normalized_destination}


async def copy_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, Any]:
    config = await get_webdav_config(tool)
    _ensure_permission(config, "allow_copy", "This WebDAV tool does not allow copying paths.")
    normalized_source = normalize_webdav_path(source_path)
    normalized_destination = normalize_webdav_path(destination_path)
    destination_full = join_webdav_paths(config["root_path"], normalized_destination)
    destination_url = build_webdav_url(config["server_url"], destination_full)
    status, _ = await webdav_request(
        config,
        "COPY",
        normalized_source,
        headers={
            "Destination": destination_url,
            "Overwrite": "T" if overwrite else "F",
        },
        expected_statuses={201, 204},
    )
    return {"status": "ok", "http_status": status, "path": normalized_destination}


async def walk_paths(
    tool: Tool,
    *,
    start_path: str,
    term: str = "",
    limit: int = WEBDAV_MAX_RECURSIVE_PATHS,
) -> tuple[list[str], int]:
    normalized_start = normalize_webdav_path(start_path)
    lowered_term = str(term or "").lower()
    matches: list[str] = []
    examined = 0

    metadata = await stat_path(tool, normalized_start)
    if not metadata.get("exists"):
        return [], examined

    def _matches(path_value: str) -> bool:
        return not lowered_term or lowered_term in posixpath.basename(path_value).lower()

    if _matches(normalized_start):
        matches.append(normalized_start)

    if metadata.get("type") != "directory":
        return sorted(set(matches)), examined

    queue = [normalized_start]
    while queue:
        current = queue.pop(0)
        for entry in await list_directory(tool, current):
            examined += 1
            if examined > limit:
                raise ValueError(
                    f"WebDAV recursive traversal exceeded {limit} paths. "
                    "Please target a smaller sub-directory."
                )
            entry_path = entry["path"]
            if _matches(entry_path):
                matches.append(entry_path)
            if entry["type"] == "directory":
                queue.append(entry_path)

    return sorted(set(matches)), examined


async def find_paths(
    tool: Tool,
    *,
    start_path: str,
    term: str = "",
    limit: int = WEBDAV_MAX_RECURSIVE_PATHS,
) -> list[str]:
    matches, _examined = await walk_paths(tool, start_path=start_path, term=term, limit=limit)
    return matches
