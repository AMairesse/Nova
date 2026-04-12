from __future__ import annotations

from asgiref.sync import async_to_sync

from nova.file_utils import (
    batch_upload_files,
    build_message_attachment_path,
)
from nova.message_attachments import (
    build_message_attachment_manifest_from_user_file,
    build_attachment_label,
    detect_attachment_kind,
    format_message_attachment_size_label,
    get_message_attachment_max_audio_size_bytes,
    get_message_attachment_max_document_size_bytes,
    get_message_attachment_max_files,
    get_message_attachment_max_image_size_bytes,
    normalize_message_attachments,
)
from nova.models.UserFile import UserFile


def upload_message_attachments(thread, user, message, uploaded_files) -> tuple[list[dict], list[str]]:
    uploaded_files = list(uploaded_files or [])
    if not uploaded_files:
        return [], []

    max_files = get_message_attachment_max_files()
    max_image_size = get_message_attachment_max_image_size_bytes()
    if len(uploaded_files) > max_files:
        return [], [f"You can attach up to {max_files} attachments per message."]

    file_data = []
    max_upload_size = 0
    for uploaded_file in uploaded_files:
        guessed_kind = detect_attachment_kind(
            getattr(uploaded_file, "content_type", None),
            getattr(uploaded_file, "name", None),
        )
        if guessed_kind == "image":
            max_size_bytes = max_image_size
            too_large_label = "Image"
        elif guessed_kind == "pdf":
            max_size_bytes = get_message_attachment_max_document_size_bytes()
            too_large_label = "PDF"
        elif guessed_kind == "audio":
            max_size_bytes = get_message_attachment_max_audio_size_bytes()
            too_large_label = "Audio"
        else:
            return [], [f"Unsupported attachment type: {uploaded_file.name}"]

        if uploaded_file.size > max_size_bytes:
            max_size_label = format_message_attachment_size_label(max_size_bytes)
            return [], [f"{too_large_label} too large: {uploaded_file.name} ({max_size_label} max)"]
        try:
            content = uploaded_file.read()
        except Exception as exc:
            return [], [f"Attachment upload failed while reading {uploaded_file.name}: {exc}"]

        file_data.append(
            {
                "path": build_message_attachment_path(message.id, uploaded_file.name),
                "content": content,
            }
        )
        max_upload_size = max(max_upload_size, max_size_bytes)

    created_files, errors = async_to_sync(batch_upload_files)(
        thread,
        user,
        file_data,
        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        source_message=message,
        max_file_size=max_upload_size or max_image_size,
        allowed_mime_types=["application/pdf"],
        allowed_mime_prefixes=("image/", "audio/"),
    )
    created_attachments = _build_attachment_manifests_for_uploaded_files(
        message,
        created_files,
    )
    return [
        dict(attachment)
        for attachment in created_attachments
    ], errors


def _build_attachment_manifests_for_uploaded_files(message, created_files: list[dict]) -> list[dict]:
    file_ids = []
    for item in created_files:
        try:
            file_ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            continue

    if not file_ids:
        return []

    user_files = {
        user_file.id: user_file
        for user_file in UserFile.objects.filter(
            id__in=file_ids,
            source_message=message,
            thread=message.thread,
            user=message.user,
        )
    }

    attachments = []
    for index, item in enumerate(created_files):
        try:
            file_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        user_file = user_files.get(file_id)
        if user_file is None:
            continue

        attachments.append(
            build_message_attachment_manifest_from_user_file(
                user_file,
                kind=detect_attachment_kind(
                    user_file.mime_type,
                    user_file.original_filename,
                ),
                label=build_attachment_label(user_file, fallback=f"attachment-{index + 1}"),
                metadata={"source": "message_attachment"},
            )
        )
    return attachments


def annotate_user_message(message) -> None:
    internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
    file_ids = internal_data.get("file_ids")
    if isinstance(file_ids, list):
        message.file_count = len(file_ids)
    else:
        message.file_count = 0

    attachment_manifests: list[dict] = []
    prefetched_files = getattr(message, "prefetched_message_attachments", None)

    if prefetched_files is not None:
        try:
            ordered_files = sorted(
                prefetched_files,
                key=lambda user_file: (
                    getattr(user_file, "created_at", None),
                    int(getattr(user_file, "id", 0) or 0),
                ),
            )
            attachment_manifests = [
                build_message_attachment_manifest_from_user_file(user_file)
                for user_file in ordered_files
            ]
        except Exception:
            attachment_manifests = []
    else:
        related_files = getattr(message, "attached_files", None)
        if related_files is not None:
            try:
                attachment_manifests = [
                    build_message_attachment_manifest_from_user_file(user_file)
                    for user_file in related_files.filter(
                        scope=UserFile.Scope.MESSAGE_ATTACHMENT
                    ).order_by("created_at", "id")
                ]
            except Exception:
                attachment_manifests = []

    message.message_attachments = normalize_message_attachments(attachment_manifests)
    message.message_attachment_count = len(message.message_attachments)
