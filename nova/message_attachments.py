from __future__ import annotations

from typing import Any
from django.conf import settings

MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY = "message_attachments"
DEFAULT_MESSAGE_ATTACHMENT_MAX_FILES = 4
DEFAULT_MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_MESSAGE_ATTACHMENT_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024
DEFAULT_MESSAGE_ATTACHMENT_MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024


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


def normalize_message_attachments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    attachments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        attachment_id = item.get("id")
        filename = str(item.get("filename") or "").strip()
        mime_type = str(item.get("mime_type") or "").strip()
        scope = str(item.get("scope") or "").strip()
        try:
            attachment_id = int(attachment_id)
        except (TypeError, ValueError):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        attachments.append(
            {
                "id": attachment_id,
                "filename": filename,
                "mime_type": mime_type,
                "size": max(0, size),
                "scope": scope,
            }
        )
    return attachments


def build_message_attachment_metadata(created_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for item in created_files:
        if not isinstance(item, dict):
            continue
        try:
            file_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        metadata.append(
            {
                "id": file_id,
                "filename": str(item.get("filename") or "").strip(),
                "mime_type": str(item.get("mime_type") or "").strip(),
                "size": int(item.get("size") or 0),
                "scope": str(item.get("scope") or "").strip(),
            }
        )
    return metadata
