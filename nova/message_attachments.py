from __future__ import annotations

import posixpath
from typing import Any

from django.conf import settings
from django.urls import reverse

from nova.models.UserFile import UserFile


class AttachmentKind:
    IMAGE = "image"
    PDF = "pdf"
    AUDIO = "audio"
    TEXT = "text"
    ANNOTATION = "annotation"


DEFAULT_MESSAGE_ATTACHMENT_MAX_FILES = 4
DEFAULT_MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_MESSAGE_ATTACHMENT_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024
DEFAULT_MESSAGE_ATTACHMENT_MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024
MESSAGE_ATTACHMENT_INBOX_ROOT = "/inbox"
MESSAGE_ATTACHMENT_HISTORY_ROOT = "/history"


def get_message_attachment_max_files() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "MESSAGE_ATTACHMENT_MAX_FILES",
                DEFAULT_MESSAGE_ATTACHMENT_MAX_FILES,
            )
        ),
    )


def get_message_attachment_max_image_size_bytes() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES",
                DEFAULT_MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES,
            )
        ),
    )


def get_message_attachment_max_document_size_bytes() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "MESSAGE_ATTACHMENT_MAX_DOCUMENT_SIZE_BYTES",
                DEFAULT_MESSAGE_ATTACHMENT_MAX_DOCUMENT_SIZE_BYTES,
            )
        ),
    )


def get_message_attachment_max_audio_size_bytes() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "MESSAGE_ATTACHMENT_MAX_AUDIO_SIZE_BYTES",
                DEFAULT_MESSAGE_ATTACHMENT_MAX_AUDIO_SIZE_BYTES,
            )
        ),
    )


def format_message_attachment_size_label(size_bytes: int | None = None) -> str:
    size_bytes = int(
        get_message_attachment_max_image_size_bytes()
        if size_bytes is None
        else size_bytes
    )
    mib = 1024 * 1024
    kib = 1024

    if size_bytes >= mib:
        size_mb = size_bytes / mib
        if float(size_mb).is_integer():
            return f"{int(size_mb)} MB"
        return f"{size_mb:.1f} MB"

    if size_bytes >= kib:
        size_kb = size_bytes / kib
        if float(size_kb).is_integer():
            return f"{int(size_kb)} KB"
        return f"{size_kb:.1f} KB"

    return f"{size_bytes} bytes"


def get_message_attachment_template_context() -> dict[str, Any]:
    max_files = get_message_attachment_max_files()
    max_image_size_bytes = get_message_attachment_max_image_size_bytes()
    max_document_size_bytes = get_message_attachment_max_document_size_bytes()
    max_audio_size_bytes = get_message_attachment_max_audio_size_bytes()
    return {
        "message_attachment_max_files": max_files,
        "message_attachment_max_image_size_bytes": max_image_size_bytes,
        "message_attachment_max_image_size_label": format_message_attachment_size_label(
            max_image_size_bytes
        ),
        "message_attachment_max_document_size_bytes": max_document_size_bytes,
        "message_attachment_max_document_size_label": format_message_attachment_size_label(
            max_document_size_bytes
        ),
        "message_attachment_max_audio_size_bytes": max_audio_size_bytes,
        "message_attachment_max_audio_size_label": format_message_attachment_size_label(
            max_audio_size_bytes
        ),
    }


def detect_attachment_kind(
    mime_type: str | None,
    filename: str | None = None,
) -> str:
    normalized_mime = str(mime_type or "").strip().lower()
    normalized_filename = str(filename or "").strip().lower()

    if normalized_mime.startswith("image/"):
        return AttachmentKind.IMAGE
    if normalized_mime == "application/pdf" or normalized_filename.endswith(".pdf"):
        return AttachmentKind.PDF
    if normalized_mime.startswith("audio/"):
        return AttachmentKind.AUDIO
    if normalized_mime.startswith("text/") or normalized_mime in {"application/json", "text/markdown"}:
        return AttachmentKind.TEXT
    return AttachmentKind.ANNOTATION


def build_attachment_label(user_file: UserFile | None, *, fallback: str = "") -> str:
    if user_file is not None:
        normalized = str(user_file.original_filename or "").rsplit("/", 1)[-1].strip()
        if normalized:
            return normalized
    return fallback or "attachment"


def build_message_attachment_inbox_paths(user_files: list[UserFile]) -> dict[int, str]:
    aliases: dict[int, str] = {}
    used_names: dict[str, int] = {}
    for user_file in user_files:
        file_id = getattr(user_file, "id", None)
        if file_id is None:
            continue
        raw_name = build_attachment_label(user_file, fallback=f"attachment-{file_id}")
        stem, suffix = posixpath.splitext(raw_name)
        count = used_names.get(raw_name, 0)
        alias_name = raw_name if count == 0 else f"{stem}-{count + 1}{suffix}"
        used_names[raw_name] = count + 1
        aliases[file_id] = f"{MESSAGE_ATTACHMENT_INBOX_ROOT}/{alias_name}"
    return aliases


def build_message_attachment_history_paths(user_files: list[UserFile]) -> dict[int, str]:
    aliases: dict[int, str] = {}
    used_names_by_message: dict[int, dict[str, int]] = {}
    for user_file in user_files:
        file_id = getattr(user_file, "id", None)
        source_message_id = getattr(user_file, "source_message_id", None)
        if file_id is None or source_message_id is None:
            continue
        raw_name = build_attachment_label(user_file, fallback=f"attachment-{file_id}")
        stem, suffix = posixpath.splitext(raw_name)
        used_names = used_names_by_message.setdefault(int(source_message_id), {})
        count = used_names.get(raw_name, 0)
        alias_name = raw_name if count == 0 else f"{stem}-{count + 1}{suffix}"
        used_names[raw_name] = count + 1
        aliases[file_id] = (
            f"{MESSAGE_ATTACHMENT_HISTORY_ROOT}/message-{int(source_message_id)}/{alias_name}"
        )
    return aliases


def build_message_attachment_manifest_from_user_file(
    user_file: UserFile,
    *,
    kind: str | None = None,
    label: str = "",
    summary_text: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attachment_kind = kind or detect_attachment_kind(
        getattr(user_file, "mime_type", None),
        getattr(user_file, "original_filename", None),
    )
    content_url = reverse("file_content", args=[user_file.id])
    return {
        "id": int(user_file.id),
        "message_id": user_file.source_message_id,
        "user_file_id": user_file.id,
        "kind": attachment_kind,
        "mime_type": str(user_file.mime_type or "").strip(),
        "label": str(label or "").strip() or build_attachment_label(user_file),
        "summary_text": str(summary_text or "").strip(),
        "size": int(getattr(user_file, "size", 0) or 0),
        "content_url": content_url,
        "preview_url": content_url,
        "metadata": dict(metadata or {}),
    }


def normalize_message_attachments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    attachments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        try:
            attachment_id = int(item.get("id") or item.get("user_file_id") or 0)
        except (TypeError, ValueError):
            attachment_id = 0
        try:
            user_file_id = int(item.get("user_file_id") or 0) or None
        except (TypeError, ValueError):
            user_file_id = None
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        attachments.append(
            {
                "id": attachment_id or user_file_id,
                "message_id": item.get("message_id"),
                "user_file_id": user_file_id,
                "kind": str(item.get("kind") or "").strip(),
                "mime_type": str(item.get("mime_type") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "summary_text": str(item.get("summary_text") or "").strip(),
                "size": max(0, size),
                "content_url": str(item.get("content_url") or "").strip(),
                "preview_url": str(item.get("preview_url") or "").strip(),
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            }
        )
    return attachments
