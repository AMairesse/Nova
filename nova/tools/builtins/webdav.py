import logging
from typing import Any, Dict, List, Optional

from django.utils.translation import gettext_lazy as _
from langchain_core.tools import StructuredTool

from nova.external_files import (
    build_artifact_tool_payload,
    resolve_binary_attachments_for_ids,
    stage_external_files_as_artifacts,
)
from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool
from nova.webdav import service as webdav_service

logger = logging.getLogger(__name__)


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
        "Use webdav_import_file to bring an external binary into the current conversation as an artifact.",
        "Use webdav_export_file with artifact_ids or file_ids to push Nova files back to WebDAV.",
        "Use webdav_batch_move_paths with dry_run=true first for large reorganization tasks.",
        "Configure root_path and mutation permissions narrowly to reduce accidental destructive actions.",
    ]


def _normalize_path(path: str) -> str:
    return webdav_service.normalize_webdav_path(path)


def _join_paths(base: str, path: str) -> str:
    return webdav_service.join_webdav_paths(base, path)


def _build_webdav_url(server_url: str, full_path: str) -> str:
    return webdav_service.build_webdav_url(server_url, full_path)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    return webdav_service.coerce_bool(value, default=default)


async def _get_webdav_config(tool: Tool) -> Dict[str, Any]:
    return await webdav_service.get_webdav_config(tool)


async def _webdav_request(
    config: Dict[str, Any],
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str | bytes] = None,
    expected_statuses: Optional[set[int]] = None,
) -> tuple[int, str]:
    return await webdav_service.webdav_request(
        config,
        method,
        path,
        headers=headers,
        data=data,
        expected_statuses=expected_statuses,
    )


async def _webdav_request_binary(
    config: Dict[str, Any],
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str | bytes] = None,
    expected_statuses: Optional[set[int]] = None,
) -> tuple[int, bytes, Dict[str, str]]:
    return await webdav_service.webdav_request_binary(
        config,
        method,
        path,
        headers=headers,
        data=data,
        expected_statuses=expected_statuses,
    )


async def test_webdav_access(tool: Tool) -> Dict[str, str]:
    await webdav_service.stat_path(tool, "/")
    return {"status": "success", "message": _("WebDAV connection successful")}


async def list_files(tool: Tool, path: str = "/", depth: int = 1) -> Dict[str, Any]:
    return await webdav_service.list_files(tool, path=path, depth=depth)


async def stat_path(tool: Tool, path: str) -> Dict[str, Any]:
    return await webdav_service.stat_path(tool, path)


async def read_file(tool: Tool, path: str) -> str:
    return await webdav_service.read_text_file(tool, path)


async def import_file(tool: Tool, path: str, agent: LLMAgent):
    if agent is None or getattr(agent, "thread", None) is None:
        return _("WebDAV import requires an active conversation thread."), None

    remote_file = await webdav_service.read_binary_file(tool, path)
    filename = _normalize_path(path).rsplit("/", 1)[-1] or "webdav-file"
    artifacts, errors = await stage_external_files_as_artifacts(
        agent,
        [
            {
                "filename": filename,
                "content": remote_file["content"],
                "mime_type": remote_file["mime_type"],
                "origin_locator": {"path": path},
            }
        ],
        origin_type="webdav",
        imported_by_tool="webdav_import_file",
        source="webdav",
    )
    if errors and not artifacts:
        return _("Failed to import WebDAV file: {errors}").format(errors="; ".join(errors)), None

    artifact = artifacts[0] if artifacts else None
    message = _(
        "Imported WebDAV file %(name)s into the current conversation."
    ) % {"name": getattr(artifact, "filename", filename)}
    if errors:
        message += _(" Warnings: {errors}").format(errors="; ".join(errors))
    return message, build_artifact_tool_payload(artifacts, tool_output=True)


async def write_file(tool: Tool, path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
    return await webdav_service.write_text_file(tool, path=path, content=content, overwrite=overwrite)


async def export_file(
    tool: Tool,
    path: str,
    *,
    artifact_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    overwrite: bool = False,
    agent: LLMAgent | None = None,
) -> Dict[str, Any]:
    if agent is None or getattr(agent, "thread", None) is None:
        raise ValueError("WebDAV export requires an active conversation thread.")

    attachments = await resolve_binary_attachments_for_ids(
        user=agent.user,
        thread=agent.thread,
        artifact_ids=artifact_ids,
        file_ids=file_ids,
    )
    if not attachments:
        raise ValueError("No artifact_ids or file_ids resolved for WebDAV export.")
    if len(attachments) != 1:
        raise ValueError("webdav_export_file currently supports exporting exactly one source file.")

    attachment = attachments[0]
    destination_path = path
    if destination_path.endswith("/"):
        destination_path = _join_paths(destination_path, attachment.filename)

    result = await webdav_service.write_bytes(
        tool,
        destination_path,
        attachment.content,
        mime_type=attachment.mime_type or "application/octet-stream",
        overwrite=overwrite,
    )
    return {
        "status": result["status"],
        "http_status": result["http_status"],
        "path": destination_path,
        "filename": attachment.filename,
    }


async def create_folder(tool: Tool, path: str, recursive: bool = False) -> Dict[str, Any]:
    return await webdav_service.create_folder(tool, path, recursive=recursive)


async def delete_path(tool: Tool, path: str) -> Dict[str, Any]:
    return await webdav_service.delete_path(tool, path)


async def move_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
    return await webdav_service.move_path(tool, source_path, destination_path, overwrite=overwrite)


async def copy_path(tool: Tool, source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
    return await webdav_service.copy_path(tool, source_path, destination_path, overwrite=overwrite)


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
    config = await _get_webdav_config(tool)

    async def _list(path: str = "/", depth: int = 1) -> Dict[str, Any]:
        return await list_files(tool, path=path, depth=depth)

    async def _stat(path: str) -> Dict[str, Any]:
        return await stat_path(tool, path=path)

    async def _read(path: str) -> str:
        return await read_file(tool, path=path)

    async def _import(path: str):
        return await import_file(tool, path=path, agent=agent)

    async def _write(path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
        return await write_file(tool, path=path, content=content, overwrite=overwrite)

    async def _export(
        path: str,
        artifact_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        return await export_file(
            tool,
            path=path,
            artifact_ids=artifact_ids,
            file_ids=file_ids,
            overwrite=overwrite,
            agent=agent,
        )

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
        StructuredTool.from_function(
            coroutine=_import,
            name="webdav_import_file",
            description="Import a WebDAV file into the current conversation as an artifact.",
            return_direct=True,
            response_format="content_and_artifact",
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
        tools.append(
            StructuredTool.from_function(
                coroutine=_export,
                name="webdav_export_file",
                description="Export one Nova artifact or thread file to a WebDAV path.",
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
