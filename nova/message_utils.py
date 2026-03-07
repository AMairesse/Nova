from __future__ import annotations

from asgiref.sync import async_to_sync

from nova.file_utils import (
    batch_upload_files,
    build_message_attachment_path,
)
from nova.message_attachments import (
    MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY,
    build_message_attachment_metadata,
    format_message_attachment_size_label,
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
        return [], [f"You can attach up to {max_files} images per message."]

    file_data = []
    for uploaded_file in uploaded_files:
        if uploaded_file.size > max_image_size:
            max_size_label = format_message_attachment_size_label(max_image_size)
            return [], [f"Image too large: {uploaded_file.name} ({max_size_label} max)"]
        try:
            content = uploaded_file.read()
        except Exception as exc:
            return [], [f"Image upload failed while reading {uploaded_file.name}: {exc}"]

        file_data.append(
            {
                "path": build_message_attachment_path(message.id, uploaded_file.name),
                "content": content,
            }
        )

    created_files, errors = async_to_sync(batch_upload_files)(
        thread,
        user,
        file_data,
        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        source_message=message,
        max_file_size=max_image_size,
        allowed_mime_types=[],
        allowed_mime_prefixes=("image/",),
    )
    return build_message_attachment_metadata(created_files), errors


def annotate_user_message(message) -> None:
    internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
    file_ids = internal_data.get("file_ids")
    if isinstance(file_ids, list):
        message.file_count = len(file_ids)
    else:
        message.file_count = 0

    message.message_attachments = normalize_message_attachments(
        internal_data.get(MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY)
    )
    message.message_attachment_count = len(message.message_attachments)
