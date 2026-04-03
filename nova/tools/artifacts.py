from __future__ import annotations

import logging
from typing import Any, Tuple

from asgiref.sync import sync_to_async
from langchain_core.tools import StructuredTool

from nova.file_utils import download_file_content
from nova.message_artifacts import publish_artifact_to_files
from nova.models.MessageArtifact import MessageArtifact
from nova.realtime.sidebar_updates import publish_file_update
from nova.turn_inputs import (
    PROVIDER_DELIVERY_NATIVE_BINARY,
    PROVIDER_DELIVERY_TEXT_FALLBACK,
    get_turn_input_capability_error,
    resolve_runtime_provider,
    should_use_pdf_text_fallback,
)

logger = logging.getLogger(__name__)

METADATA = {
    "name": "Artifacts",
    "description": "Inspect and reuse multimodal artifacts created during conversation turns.",
    "loading": {
        "mode": "skill",
        "skill_id": "artifacts",
        "skill_label": "Artifacts",
    },
}


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    return [
        "Use artifact_search or artifact_ls to discover reusable message artifacts.",
        "Always call artifact_ls or artifact_search before using artifact_ids.",
        "Never guess artifact_ids.",
        "Use artifact_attach to bring a past artifact back into the current turn without copying it into Files.",
        "Use artifact_publish_to_files only when a generated artifact should become a visible thread file.",
        "Temporary files imported from mail, web, or WebDAV appear here first before any optional promotion to Files.",
    ]


async def _load_thread_artifact(agent, artifact_id: int) -> MessageArtifact:
    def _get():
        return MessageArtifact.objects.select_related("user_file", "message").get(
            id=artifact_id,
            thread=agent.thread,
            user=agent.user,
        )

    return await sync_to_async(_get, thread_sensitive=True)()


async def artifact_ls(agent, kind: str = "", limit: int = 20) -> str:
    def _list():
        queryset = MessageArtifact.objects.filter(thread=agent.thread, user=agent.user)
        if kind:
            queryset = queryset.filter(kind=kind)
        return list(
            queryset.select_related("user_file", "message")
            .order_by("-created_at", "-id")[: max(1, min(int(limit or 20), 50))]
        )

    artifacts = await sync_to_async(_list, thread_sensitive=True)()
    if not artifacts:
        return "No artifacts available in this conversation."
    lines = []
    for artifact in artifacts:
        lines.append(
            f"ID: {artifact.id}, Kind: {artifact.kind}, Direction: {artifact.direction}, "
            f"Label: {artifact.filename}, Message: {artifact.message_id}"
        )
    return "\n".join(lines)


async def artifact_search(agent, query: str, kind: str = "", limit: int = 10) -> str:
    needle = (query or "").strip()
    if not needle:
        return await artifact_ls(agent, kind=kind, limit=limit)

    def _search():
        queryset = MessageArtifact.objects.filter(thread=agent.thread, user=agent.user)
        if kind:
            queryset = queryset.filter(kind=kind)
        queryset = queryset.filter(
            models.Q(label__icontains=needle)
            | models.Q(summary_text__icontains=needle)
            | models.Q(search_text__icontains=needle)
            | models.Q(mime_type__icontains=needle)
        )
        return list(
            queryset.select_related("user_file", "message")
            .order_by("-created_at", "-id")[: max(1, min(int(limit or 10), 25))]
        )

    from django.db import models

    artifacts = await sync_to_async(_search, thread_sensitive=True)()
    if not artifacts:
        return f"No artifacts matched `{needle}`."

    return "\n".join(
        [
            f"ID: {artifact.id}, Kind: {artifact.kind}, Label: {artifact.filename}, Message: {artifact.message_id}"
            for artifact in artifacts
        ]
    )


async def artifact_get(agent, artifact_id: int) -> str:
    artifact = await _load_thread_artifact(agent, artifact_id)
    details = [
        f"Artifact {artifact.id}",
        f"Kind: {artifact.kind}",
        f"Direction: {artifact.direction}",
        f"Label: {artifact.filename}",
        f"MIME: {artifact.mime_type or 'n/a'}",
        f"Message: {artifact.message_id}",
    ]
    if artifact.user_file_id:
        details.append(f"User file: {artifact.user_file_id}")
        details.append(f"Size: {artifact.user_file.size} bytes")
    if artifact.summary_text:
        details.append(f"Summary: {artifact.summary_text}")
    return "\n".join(details)


async def artifact_read_text(agent, artifact_id: int) -> str:
    artifact = await _load_thread_artifact(agent, artifact_id)
    if artifact.summary_text:
        return artifact.summary_text

    if artifact.user_file_id and (
        artifact.mime_type.startswith("text/")
        or artifact.mime_type in {"application/json", "text/markdown"}
    ):
        raw = await download_file_content(artifact.user_file)
        return raw.decode("utf-8", errors="ignore")

    return "This artifact has no extracted text content."


async def artifact_attach(agent, artifact_id: int) -> Tuple[str, Any]:
    artifact = await _load_thread_artifact(agent, artifact_id)
    provider = resolve_runtime_provider(agent)
    capability_error = get_turn_input_capability_error(provider, artifact.kind)
    if capability_error:
        return capability_error, None

    provider_delivery = PROVIDER_DELIVERY_NATIVE_BINARY
    message = (
        f"Artifact attached: {artifact.filename}. Continue your reasoning with this artifact included."
    )
    if artifact.kind == "pdf" and should_use_pdf_text_fallback(provider):
        provider_delivery = PROVIDER_DELIVERY_TEXT_FALLBACK
        message = (
            f"Artifact attached: {artifact.filename}. Native PDF input is not verified "
            "for this model, so Nova will use extracted PDF text for this turn."
        )
    return (
        message,
        {
            "artifact_id": artifact.id,
            "kind": artifact.kind,
            "label": artifact.filename,
            "mime_type": artifact.mime_type,
            "provider_delivery": provider_delivery,
        },
    )


async def artifact_publish_to_files(agent, artifact_id: int, filename: str = "") -> str:
    artifact = await _load_thread_artifact(agent, artifact_id)
    file_id, errors = await publish_artifact_to_files(artifact, filename=filename)
    if errors and not file_id:
        return f"Failed to publish artifact: {'; '.join(errors)}"

    await publish_file_update(agent.thread.id, "artifact_publish")
    return f"Artifact published to Files as file ID {file_id}."


async def get_functions(agent) -> list[StructuredTool]:
    if agent.thread is None:
        return []

    async def artifact_ls_wrapper(kind: str = "", limit: int = 20) -> str:
        return await artifact_ls(agent, kind=kind, limit=limit)

    async def artifact_search_wrapper(query: str, kind: str = "", limit: int = 10) -> str:
        return await artifact_search(agent, query=query, kind=kind, limit=limit)

    async def artifact_get_wrapper(artifact_id: int) -> str:
        return await artifact_get(agent, artifact_id)

    async def artifact_read_text_wrapper(artifact_id: int) -> str:
        return await artifact_read_text(agent, artifact_id)

    async def artifact_attach_wrapper(artifact_id: int) -> Tuple[str, Any]:
        return await artifact_attach(agent, artifact_id)

    async def artifact_publish_wrapper(artifact_id: int, filename: str = "") -> str:
        return await artifact_publish_to_files(agent, artifact_id, filename=filename)

    return [
        StructuredTool.from_function(
            coroutine=artifact_ls_wrapper,
            name="artifact_ls",
            description="List reusable artifacts available in the current conversation.",
            args_schema={
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        ),
        StructuredTool.from_function(
            coroutine=artifact_search_wrapper,
            name="artifact_search",
            description="Search conversation artifacts by name, summary, or mime type.",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        ),
        StructuredTool.from_function(
            coroutine=artifact_get_wrapper,
            name="artifact_get",
            description="Inspect metadata for a specific artifact.",
            args_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "integer"}},
                "required": ["artifact_id"],
            },
        ),
        StructuredTool.from_function(
            coroutine=artifact_read_text_wrapper,
            name="artifact_read_text",
            description="Read extracted text or transcript content from an artifact.",
            args_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "integer"}},
                "required": ["artifact_id"],
            },
        ),
        StructuredTool.from_function(
            coroutine=artifact_attach_wrapper,
            name="artifact_attach",
            description="Attach a previous artifact to the current turn so the model can inspect it again.",
            args_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "integer"}},
                "required": ["artifact_id"],
            },
            return_direct=True,
            response_format="content_and_artifact",
        ),
        StructuredTool.from_function(
            coroutine=artifact_publish_wrapper,
            name="artifact_publish_to_files",
            description="Publish a conversation artifact into the thread Files space.",
            args_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "integer"},
                    "filename": {"type": "string"},
                },
                "required": ["artifact_id"],
            },
        ),
    ]
