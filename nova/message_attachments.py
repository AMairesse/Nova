from __future__ import annotations

from typing import Any

MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY = "message_attachments"


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
