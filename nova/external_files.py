from __future__ import annotations

import mimetypes
import posixpath
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.translation import gettext as _

from nova.file_utils import (
    batch_upload_files,
    build_message_artifact_output_path,
    detect_mime,
    download_file_content,
)
from nova.message_artifacts import build_artifact_label, detect_artifact_kind
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.UserFile import UserFile


DEFAULT_EXTERNAL_FILE_IMPORT_MAX_SIZE_BYTES = 10 * 1024 * 1024
_DANGEROUS_MIME_TYPES = {
    "application/javascript",
    "application/x-bat",
    "application/x-csh",
    "application/x-dosexec",
    "application/x-executable",
    "application/x-mach-binary",
    "application/x-msdos-program",
    "application/x-msdownload",
    "application/x-msi",
    "application/x-powershell",
    "application/x-python-code",
    "application/x-sh",
    "application/x-shellscript",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/html",
    "text/javascript",
}
_DANGEROUS_EXTENSIONS = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".csh",
    ".dmg",
    ".exe",
    ".hta",
    ".html",
    ".htm",
    ".jar",
    ".js",
    ".jse",
    ".mjs",
    ".msi",
    ".pkg",
    ".ps1",
    ".py",
    ".rb",
    ".scr",
    ".sh",
    ".svg",
    ".vbs",
    ".xhtml",
}
AUTO_ATTACH_ARTIFACT_KINDS = {
    ArtifactKind.IMAGE,
    ArtifactKind.PDF,
    ArtifactKind.AUDIO,
}


@dataclass(slots=True)
class ResolvedBinaryAttachment:
    filename: str
    mime_type: str
    content: bytes
    artifact_id: int | None = None
    file_id: int | None = None
    user_file_id: int | None = None


def get_external_file_import_max_size_bytes() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "EXTERNAL_FILE_IMPORT_MAX_SIZE_BYTES",
                DEFAULT_EXTERNAL_FILE_IMPORT_MAX_SIZE_BYTES,
            )
        ),
    )


def normalize_external_filename(filename: str | None, *, fallback: str) -> str:
    base = posixpath.basename(str(filename or "").strip())
    return base or fallback


def get_external_file_block_reason(
    *,
    filename: str,
    mime_type: str = "",
) -> str | None:
    normalized_name = normalize_external_filename(filename, fallback="downloaded-file")
    normalized_mime = str(mime_type or "").strip().lower()
    ext = posixpath.splitext(normalized_name)[1].lower()

    if normalized_mime in _DANGEROUS_MIME_TYPES:
        return _(
            "Blocked unsafe external file type %(mime)s."
        ) % {"mime": normalized_mime}

    if ext in _DANGEROUS_EXTENSIONS:
        return _(
            "Blocked unsafe external file extension %(ext)s."
        ) % {"ext": ext}

    return None


def _default_summary_text(
    *,
    origin_type: str,
    filename: str,
    mime_type: str,
    size: int,
) -> str:
    mime_label = mime_type or "application/octet-stream"
    return _(
        "Imported file %(filename)s from %(origin)s (%(mime)s, %(size)s bytes)."
    ) % {
        "filename": filename,
        "origin": origin_type,
        "mime": mime_label,
        "size": int(size or 0),
    }


async def _create_hidden_tool_message(agent, *, origin_type: str):
    if getattr(agent, "thread", None) is None or getattr(agent, "user", None) is None:
        raise ValueError("External file staging requires an active conversation thread.")

    hidden_message = await sync_to_async(
        agent.thread.add_message,
        thread_sensitive=True,
    )(
        f"Hidden external tool output ({origin_type})",
        Actor.SYSTEM,
    )
    hidden_message.internal_data = {
        "hidden_tool_output": True,
        "origin_type": origin_type,
    }
    await sync_to_async(hidden_message.save, thread_sensitive=True)(
        update_fields=["internal_data"]
    )
    return hidden_message


async def stage_external_files_as_artifacts(
    agent,
    files: list[dict[str, Any]],
    *,
    origin_type: str,
    imported_by_tool: str,
    source: str | None = None,
) -> tuple[list[MessageArtifact], list[str]]:
    normalized_origin = str(origin_type or "").strip().lower() or "external"
    normalized_source = str(source or normalized_origin).strip() or normalized_origin
    max_size = get_external_file_import_max_size_bytes()
    errors: list[str] = []

    valid_specs: list[dict[str, Any]] = []
    for index, item in enumerate(list(files or []), start=1):
        raw_content = item.get("content")
        if isinstance(raw_content, bytearray):
            raw_content = bytes(raw_content)
        if not isinstance(raw_content, bytes) or not raw_content:
            errors.append(f"Empty content for external file #{index}.")
            continue

        filename = normalize_external_filename(
            item.get("filename"),
            fallback=f"{normalized_origin}-{index}",
        )
        declared_mime_type = str(item.get("mime_type") or "").strip().lower()
        detected_mime_type = str(detect_mime(raw_content) or "").strip().lower()
        mime_type = declared_mime_type or detected_mime_type
        block_reason = get_external_file_block_reason(
            filename=filename,
            mime_type=detected_mime_type or mime_type,
        )
        if not block_reason and declared_mime_type:
            block_reason = get_external_file_block_reason(
                filename=filename,
                mime_type=declared_mime_type,
            )
        if block_reason:
            errors.append(f"{filename}: {block_reason}")
            continue
        if len(raw_content) > max_size:
            errors.append(
                f"{filename}: external file exceeds the {max_size} byte import limit."
            )
            continue

        valid_specs.append(
            {
                "filename": filename,
                "content": raw_content,
                "mime_type": mime_type,
                "label": str(item.get("label") or "").strip(),
                "summary_text": str(item.get("summary_text") or "").strip(),
                "search_text": str(item.get("search_text") or "").strip(),
                "metadata": dict(item.get("metadata") or {}),
                "origin_locator": dict(item.get("origin_locator") or {}),
            }
        )

    if not valid_specs:
        return [], errors

    hidden_message = await _create_hidden_tool_message(
        agent,
        origin_type=normalized_origin,
    )
    upload_specs = [
        {
            "path": build_message_artifact_output_path(
                hidden_message.id,
                spec["filename"],
            ),
            "content": spec["content"],
            "mime_type": spec["mime_type"],
        }
        for spec in valid_specs
    ]

    created_files, upload_errors = await batch_upload_files(
        agent.thread,
        agent.user,
        upload_specs,
        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        source_message=hidden_message,
        max_file_size=max_size,
    )
    errors.extend(list(upload_errors or []))
    if not created_files:
        return [], errors

    created_ids: list[int] = []
    for item in created_files:
        try:
            created_ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            continue

    def _load_files():
        return {
            user_file.id: user_file
            for user_file in UserFile.objects.filter(
                id__in=created_ids,
                user=agent.user,
                thread=agent.thread,
                source_message=hidden_message,
            )
        }

    user_files = await sync_to_async(_load_files, thread_sensitive=True)()
    created_artifacts: list[MessageArtifact] = []

    for index, file_meta in enumerate(created_files):
        try:
            file_id = int(file_meta.get("id"))
        except (TypeError, ValueError):
            continue

        user_file = user_files.get(file_id)
        if user_file is None:
            continue

        spec = valid_specs[index] if index < len(valid_specs) else {}
        detected_kind = detect_artifact_kind(
            user_file.mime_type,
            user_file.original_filename,
        )
        artifact_kind = (
            detected_kind
            if detected_kind in AUTO_ATTACH_ARTIFACT_KINDS
            else ArtifactKind.ANNOTATION
        )
        metadata = dict(spec.get("metadata") or {})
        metadata.update(
            {
                "source": normalized_source,
                "origin_type": normalized_origin,
                "origin_locator": dict(spec.get("origin_locator") or {}),
                "imported_by_tool": imported_by_tool,
            }
        )

        label = str(spec.get("label") or "").strip() or build_artifact_label(
            user_file,
            fallback=f"artifact-{file_id}",
        )
        summary_text = str(spec.get("summary_text") or "").strip()
        if artifact_kind == ArtifactKind.ANNOTATION and not summary_text:
            summary_text = _default_summary_text(
                origin_type=normalized_origin,
                filename=label,
                mime_type=user_file.mime_type or str(spec.get("mime_type") or ""),
                size=int(getattr(user_file, "size", 0) or 0),
            )
        search_text = str(spec.get("search_text") or "").strip() or label

        artifact = await sync_to_async(
            MessageArtifact.objects.create,
            thread_sensitive=True,
        )(
            user=agent.user,
            thread=agent.thread,
            message=hidden_message,
            user_file=user_file,
            direction=ArtifactDirection.OUTPUT,
            kind=artifact_kind,
            mime_type=user_file.mime_type or "",
            label=label,
            summary_text=summary_text,
            search_text=search_text,
            metadata=metadata,
            order=index,
        )
        created_artifacts.append(artifact)

    return created_artifacts, errors


def build_artifact_tool_payload(
    artifacts: list[MessageArtifact],
    *,
    tool_output: bool = True,
) -> dict[str, Any]:
    refs = []
    for artifact in list(artifacts or []):
        refs.append(
            {
                "artifact_id": int(getattr(artifact, "id", 0) or 0),
                "kind": str(getattr(artifact, "kind", "") or "").strip(),
                "label": str(getattr(artifact, "filename", "") or "").strip(),
                "mime_type": str(getattr(artifact, "mime_type", "") or "").strip(),
                "tool_output": bool(tool_output),
                "auto_attach": str(getattr(artifact, "kind", "") or "").strip()
                in AUTO_ATTACH_ARTIFACT_KINDS,
            }
        )
    return {"artifact_refs": [ref for ref in refs if ref.get("artifact_id")]}


async def _load_thread_shared_files(user, thread, file_ids: list[int]) -> dict[int, UserFile]:
    def _load():
        return {
            user_file.id: user_file
            for user_file in UserFile.objects.filter(
                id__in=file_ids,
                user=user,
                thread=thread,
                scope=UserFile.Scope.THREAD_SHARED,
            )
        }

    return await sync_to_async(_load, thread_sensitive=True)()


async def _load_thread_artifacts(user, thread, artifact_ids: list[int]) -> dict[int, MessageArtifact]:
    def _load():
        return {
            artifact.id: artifact
            for artifact in MessageArtifact.objects.select_related(
                "user_file",
                "published_file",
                "source_artifact",
            ).filter(
                id__in=artifact_ids,
                user=user,
                thread=thread,
            )
        }

    return await sync_to_async(_load, thread_sensitive=True)()


async def _load_scoped_user_file(
    *,
    user_id: int | None,
    thread_id: int | None,
    file_id: int | None,
) -> UserFile | None:
    if not user_id or not thread_id or not file_id:
        return None

    def _load():
        return UserFile.objects.filter(
            id=file_id,
            user_id=user_id,
            thread_id=thread_id,
        ).first()

    return await sync_to_async(_load, thread_sensitive=True)()


async def _resolve_artifact_source_user_file(
    artifact: MessageArtifact,
) -> UserFile | None:
    owner_user_id = getattr(artifact, "user_id", None)
    owner_thread_id = getattr(artifact, "thread_id", None)
    seen_ids: set[int] = set()
    current = artifact
    while current is not None and getattr(current, "id", None) not in seen_ids:
        current_id = getattr(current, "id", None)
        if current_id:
            seen_ids.add(current_id)

        published_file = await _load_scoped_user_file(
            user_id=owner_user_id,
            thread_id=owner_thread_id,
            file_id=getattr(current, "published_file_id", None),
        )
        if published_file is not None:
            return published_file

        user_file = await _load_scoped_user_file(
            user_id=owner_user_id,
            thread_id=owner_thread_id,
            file_id=getattr(current, "user_file_id", None),
        )
        if user_file is not None:
            return user_file

        source_artifact_id = getattr(current, "source_artifact_id", None)
        if not source_artifact_id:
            return None

        def _load_source():
            return (
                MessageArtifact.objects.select_related(
                    "user_file",
                    "published_file",
                    "source_artifact",
                )
                .filter(
                    id=source_artifact_id,
                    user_id=owner_user_id,
                    thread_id=owner_thread_id,
                )
                .first()
            )

        current = await sync_to_async(_load_source, thread_sensitive=True)()

    return None


def _ensure_attachment_filename(filename: str, mime_type: str) -> str:
    normalized = normalize_external_filename(filename, fallback="attachment")
    if posixpath.splitext(normalized)[1]:
        return normalized

    guessed_ext = mimetypes.guess_extension(str(mime_type or "").strip().lower()) or ""
    if guessed_ext:
        return f"{normalized}{guessed_ext}"
    return normalized


async def resolve_binary_attachments_for_ids(
    *,
    user,
    thread,
    artifact_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[ResolvedBinaryAttachment]:
    normalized_artifact_ids: list[int] = []
    for artifact_id in list(artifact_ids or []):
        try:
            value = int(artifact_id)
        except (TypeError, ValueError):
            continue
        if value not in normalized_artifact_ids:
            normalized_artifact_ids.append(value)

    normalized_file_ids: list[int] = []
    for file_id in list(file_ids or []):
        try:
            value = int(file_id)
        except (TypeError, ValueError):
            continue
        if value not in normalized_file_ids:
            normalized_file_ids.append(value)

    if (normalized_artifact_ids or normalized_file_ids) and thread is None:
        raise ValueError("Attachments require an active conversation thread.")

    resolved: list[ResolvedBinaryAttachment] = []
    seen_keys: set[tuple[str, int]] = set()

    if normalized_artifact_ids:
        artifacts = await _load_thread_artifacts(user, thread, normalized_artifact_ids)
        missing_artifact_ids = [
            artifact_id
            for artifact_id in normalized_artifact_ids
            if artifact_id not in artifacts
        ]
        if missing_artifact_ids:
            raise ValueError(
                "Artifact(s) not found or not accessible: "
                + ", ".join(str(artifact_id) for artifact_id in missing_artifact_ids)
            )

        for artifact_id in normalized_artifact_ids:
            artifact = artifacts[artifact_id]
            source_file = await _resolve_artifact_source_user_file(artifact)
            if source_file is not None:
                key = ("user_file", int(source_file.id))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                resolved.append(
                    ResolvedBinaryAttachment(
                        filename=_ensure_attachment_filename(
                            build_artifact_label(
                                source_file,
                                fallback=f"artifact-{artifact.id}",
                            ),
                            source_file.mime_type or artifact.mime_type or "",
                        ),
                        mime_type=source_file.mime_type or artifact.mime_type or "application/octet-stream",
                        content=await download_file_content(source_file),
                        artifact_id=artifact.id,
                        user_file_id=source_file.id,
                    )
                )
                continue

            summary_text = str(getattr(artifact, "summary_text", "") or "").strip()
            if not summary_text:
                raise ValueError(
                    f"Artifact {artifact.id} has no binary file or text content to export."
                )
            key = ("artifact", int(artifact.id))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            resolved.append(
                ResolvedBinaryAttachment(
                    filename=_ensure_attachment_filename(
                        getattr(artifact, "filename", "") or f"artifact-{artifact.id}",
                        "text/plain",
                    ),
                    mime_type="text/plain",
                    content=summary_text.encode("utf-8"),
                    artifact_id=artifact.id,
                )
            )

    if normalized_file_ids:
        files = await _load_thread_shared_files(user, thread, normalized_file_ids)
        missing_file_ids = [
            file_id for file_id in normalized_file_ids if file_id not in files
        ]
        if missing_file_ids:
            raise ValueError(
                "File(s) not found or not accessible: "
                + ", ".join(str(file_id) for file_id in missing_file_ids)
            )

        for file_id in normalized_file_ids:
            user_file = files[file_id]
            key = ("user_file", int(user_file.id))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            resolved.append(
                ResolvedBinaryAttachment(
                    filename=_ensure_attachment_filename(
                        build_artifact_label(
                            user_file,
                            fallback=f"file-{user_file.id}",
                        ),
                        user_file.mime_type or "",
                    ),
                    mime_type=user_file.mime_type or "application/octet-stream",
                    content=await download_file_content(user_file),
                    file_id=user_file.id,
                    user_file_id=user_file.id,
                )
            )

    return resolved
