from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import UploadedFile

from nova.file_utils import batch_upload_files
from nova.message_attachments import normalize_message_attachments
from nova.message_utils import annotate_user_message, upload_message_attachments
from nova.models.Message import Message
from nova.models.Task import Task
from nova.models.Thread import Thread
from nova.realtime.sidebar_updates import publish_file_update
from nova.views.agent_dispatch import (
    enqueue_message_agent_task,
    get_agent_execution_capability_error,
    get_message_attachment_capability_error,
    resolve_selected_or_default_agent,
)

logger = logging.getLogger(__name__)


class MessageSubmissionError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class SubmissionContext:
    thread: Thread
    message: Message | None = None
    create_message: Callable[[str], Message] | None = None
    before_message_delete: Callable[[Message], None] | None = None
    after_dispatch: Callable[[], None] | None = None
    thread_html: str | None = None
    response_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubmissionResult:
    thread: Thread
    message: Message
    task: Task
    uploaded_file_ids: list[int]
    thread_html: str | None = None
    response_fields: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        message_data = {
            "id": self.message.id,
            "text": self.message.text,
            "actor": self.message.actor,
            "file_count": len(self.uploaded_file_ids),
            "internal_data": self.message.internal_data or {},
            "attachments": getattr(self.message, "message_attachments", []),
        }
        payload = {
            "status": "OK",
            "message": message_data,
            "thread_id": self.thread.id,
            "task_id": self.task.id,
            "threadHtml": self.thread_html,
            "uploaded_file_ids": self.uploaded_file_ids,
        }
        payload.update(self.response_fields)
        return payload


def _normalize_message_text(raw_text: str | None) -> str:
    text = str(raw_text or "")
    return text if text.strip() else ""


def _upload_thread_files(
    *,
    thread: Thread,
    user,
    uploaded_files: Sequence[UploadedFile],
    thread_file_uploader=batch_upload_files,
    file_update_publisher=publish_file_update,
) -> list[int]:
    if not uploaded_files:
        return []

    file_data = []
    for uploaded_file in uploaded_files:
        try:
            content = uploaded_file.read()
        except Exception as exc:
            logger.error("Failed reading uploaded file %s: %s", uploaded_file.name, exc)
            raise MessageSubmissionError(
                "File upload failed while reading content",
                status_code=500,
            ) from exc
        file_data.append(
            {
                "path": f"/{uploaded_file.name}",
                "content": content,
                "mime_type": str(
                    getattr(uploaded_file, "content_type", "") or ""
                ).strip().lower(),
            }
        )

    try:
        created_files, errors = async_to_sync(thread_file_uploader)(thread, user, file_data)
    except Exception as exc:
        logger.error("Batch upload failed: %s", exc)
        raise MessageSubmissionError("File upload failed", status_code=500) from exc

    uploaded_file_ids = [
        item.get("id")
        for item in list(created_files or [])
        if item.get("id")
    ]

    if uploaded_file_ids:
        async_to_sync(file_update_publisher)(thread.id, "attachment_upload")

    if errors and not uploaded_file_ids:
        raise MessageSubmissionError("; ".join(errors), status_code=400)

    return uploaded_file_ids


def _cleanup_submission_message(context: SubmissionContext) -> None:
    message = context.message
    if message is None:
        return

    if context.before_message_delete is not None:
        context.before_message_delete(message)
    message.delete()
    context.message = None


def submit_user_message(
    *,
    user,
    message_text: str | None,
    selected_agent: str | None,
    response_mode: str | None,
    thread_mode: str | None,
    thread_files: Sequence[UploadedFile] | None,
    message_attachments: Sequence[UploadedFile] | None,
    prepare_context: Callable[[str], SubmissionContext],
    dispatcher_task,
    thread_file_uploader=batch_upload_files,
    attachment_uploader=upload_message_attachments,
    file_update_publisher=publish_file_update,
) -> SubmissionResult:
    normalized_text = _normalize_message_text(message_text)
    normalized_response_mode = str(response_mode or "auto").strip().lower() or "auto"
    uploaded_thread_files = list(thread_files or [])
    uploaded_message_attachments = list(message_attachments or [])

    if not normalized_text and not uploaded_thread_files and not uploaded_message_attachments:
        raise MessageSubmissionError("Message or attachment required", status_code=400)

    agent_config = resolve_selected_or_default_agent(user, selected_agent)
    execution_error = get_agent_execution_capability_error(
        agent_config,
        thread_mode=thread_mode,
        response_mode=normalized_response_mode,
    )
    if execution_error:
        raise MessageSubmissionError(execution_error, status_code=400)

    if uploaded_message_attachments:
        attachment_error = get_message_attachment_capability_error(
            agent_config,
            uploaded_message_attachments,
        )
        if attachment_error:
            raise MessageSubmissionError(attachment_error, status_code=400)

    context = prepare_context(normalized_text)
    try:
        uploaded_file_ids = _upload_thread_files(
            thread=context.thread,
            user=user,
            uploaded_files=uploaded_thread_files,
            thread_file_uploader=thread_file_uploader,
            file_update_publisher=file_update_publisher,
        )
    except MessageSubmissionError:
        _cleanup_submission_message(context)
        raise

    message = context.message
    if message is None:
        if context.create_message is None:
            raise RuntimeError("Submission context must provide a message or a create_message callback.")
        message = context.create_message(normalized_text)
        context.message = message

    message_attachment_manifests: list[dict] = []
    if uploaded_message_attachments:
        attachment_meta, attachment_errors = attachment_uploader(
            context.thread,
            user,
            message,
            uploaded_message_attachments,
        )
        message_attachment_manifests = list(attachment_meta or [])
        if attachment_errors and not message_attachment_manifests:
            _cleanup_submission_message(context)
            raise MessageSubmissionError("; ".join(attachment_errors), status_code=400)

    message.internal_data = {
        "file_ids": uploaded_file_ids,
        "response_mode": normalized_response_mode,
    }
    message.save(update_fields=["internal_data"])
    annotate_user_message(message)
    if message_attachment_manifests and not getattr(message, "message_attachments", None):
        message.message_attachments = normalize_message_attachments(
            message_attachment_manifests
        )
        message.message_attachment_count = len(message.message_attachments)

    task = enqueue_message_agent_task(
        user=user,
        thread=context.thread,
        agent_config=agent_config,
        source_message_id=message.id,
        dispatcher_task=dispatcher_task,
    )

    if context.after_dispatch is not None:
        context.after_dispatch()

    return SubmissionResult(
        thread=context.thread,
        message=message,
        task=task,
        uploaded_file_ids=uploaded_file_ids,
        thread_html=context.thread_html,
        response_fields=dict(context.response_fields),
    )
