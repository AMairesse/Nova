from __future__ import annotations

import base64
import json
import logging
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as jsonschema_validate

from nova.mcp.client import MCPClient
from nova.models.Tool import Tool, ToolCredential

logger = logging.getLogger(__name__)


class MCPServiceError(Exception):
    pass


@dataclass(slots=True)
class ExtractedMCPArtifact:
    path: str
    content: bytes
    mime_type: str


def _jsonable_mcp_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, list):
        return [_jsonable_mcp_value(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable_mcp_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_mcp_value(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _jsonable_mcp_value(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable_mcp_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def _path_basename_from_uri(uri: str, *, fallback: str) -> str:
    parsed = urlparse(str(uri or ""))
    candidate = posixpath.basename(parsed.path or "")
    return candidate or fallback


def _normalize_extractable_entry(value: Any, *, fallback_name: str) -> ExtractedMCPArtifact | None:
    if not isinstance(value, dict):
        return None

    entry_type = str(value.get("type") or "").strip().lower()
    mime_type = str(value.get("mimeType") or value.get("mime_type") or "").strip() or "application/octet-stream"
    filename = str(value.get("filename") or value.get("name") or "").strip()
    uri = str(value.get("uri") or "").strip()
    if uri and not filename:
        filename = _path_basename_from_uri(uri, fallback=fallback_name)
    if not filename:
        filename = fallback_name

    if entry_type == "text" and "text" in value:
        return ExtractedMCPArtifact(
            path=filename if filename.endswith(".txt") else f"{filename}.txt",
            content=str(value.get("text") or "").encode("utf-8"),
            mime_type="text/plain",
        )

    if entry_type == "image" and value.get("data"):
        try:
            content = base64.b64decode(str(value.get("data") or ""))
        except Exception:
            return None
        extension = ".bin"
        if mime_type == "image/png":
            extension = ".png"
        elif mime_type == "image/jpeg":
            extension = ".jpg"
        elif mime_type == "image/webp":
            extension = ".webp"
        if not filename.endswith(extension):
            filename = f"{filename}{extension}"
        return ExtractedMCPArtifact(path=filename, content=content, mime_type=mime_type)

    if "resource" in value and isinstance(value["resource"], dict):
        return _normalize_extractable_entry(value["resource"], fallback_name=fallback_name)

    if "text" in value and (entry_type == "resource" or mime_type.startswith("text/")):
        return ExtractedMCPArtifact(
            path=filename,
            content=str(value.get("text") or "").encode("utf-8"),
            mime_type=mime_type or "text/plain",
        )

    encoded = value.get("data") or value.get("blob")
    if encoded and isinstance(encoded, str):
        try:
            content = base64.b64decode(encoded)
        except Exception:
            return None
        return ExtractedMCPArtifact(path=filename, content=content, mime_type=mime_type)

    return None


def _find_extractable_artifacts(value: Any, *, prefix: str = "artifact") -> list[ExtractedMCPArtifact]:
    artifacts: list[ExtractedMCPArtifact] = []

    def _walk(node: Any, *, stem: str) -> None:
        candidate = _normalize_extractable_entry(node, fallback_name=stem)
        if candidate is not None:
            artifacts.append(candidate)
            return
        if isinstance(node, list):
            for index, item in enumerate(node, start=1):
                _walk(item, stem=f"{stem}-{index}")
            return
        if isinstance(node, dict):
            for key, item in node.items():
                next_stem = str(key or stem).strip() or stem
                _walk(item, stem=next_stem)

    _walk(value, stem=prefix)
    return artifacts


async def _load_credential(*, tool: Tool, user) -> ToolCredential | None:
    def _load():
        return ToolCredential.objects.filter(user=user, tool=tool).first()

    return await sync_to_async(_load, thread_sensitive=True)()


async def _build_client(*, tool: Tool, user) -> MCPClient:
    credential = await _load_credential(tool=tool, user=user)
    user_id = getattr(user, "id", None)
    return MCPClient(
        endpoint=tool.endpoint,
        credential=credential,
        transport_type=tool.transport_type,
        user_id=user_id,
    )


async def list_mcp_tools(*, tool: Tool, user, force_refresh: bool = False) -> list[dict[str, Any]]:
    client = await _build_client(tool=tool, user=user)
    try:
        return await client.alist_tools(force_refresh=force_refresh)
    except Exception as exc:
        raise MCPServiceError(str(exc)) from exc


async def describe_mcp_tool(*, tool: Tool, user, tool_name: str) -> dict[str, Any]:
    available_tools = await list_mcp_tools(tool=tool, user=user, force_refresh=False)
    selector = str(tool_name or "").strip()
    for meta in available_tools:
        if str(meta.get("name") or "").strip() == selector:
            return {
                "server": {
                    "id": tool.id,
                    "name": tool.name,
                    "endpoint": tool.endpoint,
                    "transport_type": tool.transport_type,
                },
                "tool": {
                    "name": meta.get("name"),
                    "description": meta.get("description") or "",
                    "input_schema": meta.get("input_schema") or {},
                    "output_schema": meta.get("output_schema") or {},
                },
            }
    raise MCPServiceError(f"Unknown MCP tool: {selector}")


async def call_mcp_tool(
    *,
    tool: Tool,
    user,
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    description = await describe_mcp_tool(tool=tool, user=user, tool_name=tool_name)
    schema = description["tool"].get("input_schema") or {}
    if schema:
        try:
            jsonschema_validate(instance=payload, schema=schema)
        except JSONSchemaValidationError as exc:
            raise MCPServiceError(f"Input validation failed: {exc.message}") from exc

    client = await _build_client(tool=tool, user=user)
    try:
        result = await client.acall(description["tool"]["name"], **payload)
    except ValidationError as exc:
        raise MCPServiceError(str(exc)) from exc
    except Exception as exc:
        raise MCPServiceError(str(exc)) from exc

    normalized_result = _jsonable_mcp_value(result)
    extracted = _find_extractable_artifacts(normalized_result, prefix=description["tool"]["name"])
    return {
        "payload": {
            "server": description["server"],
            "tool": description["tool"],
            "input": payload,
            "result": normalized_result,
        },
        "extractable_artifacts": extracted,
    }
