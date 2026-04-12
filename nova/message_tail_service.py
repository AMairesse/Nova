from __future__ import annotations

import posixpath
from dataclasses import dataclass

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Q

from nova.continuous.utils import get_day_label_for_user
from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.ConversationEmbedding import DaySegmentEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Message
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk
from nova.models.UserFile import UserFile
from nova.realtime.sidebar_updates import publish_file_update
from nova.runtime.compaction import (
    SESSION_KEY_COMPACTED_AT,
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from nova.tasks.conversation_tasks import summarize_day_segment_task
from nova.tasks.runtime_state import reconcile_stale_running_tasks
from nova.tasks.transcript_index_tasks import index_transcript_append_task


class MessageTailDeletionError(Exception):
    pass


@dataclass(slots=True)
class MessageTailDeletionPreview:
    thread_id: int
    thread_mode: str
    anchor_message_id: int
    message_ids: list[int]
    files: list[UserFile]
    has_untracked_files: bool

    @property
    def message_count(self) -> int:
        return len(self.message_ids)

    @property
    def file_count(self) -> int:
        return len(self.files)

    def serialize(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "thread_mode": self.thread_mode,
            "anchor_message_id": self.anchor_message_id,
            "message_count": self.message_count,
            "message_ids": list(self.message_ids),
            "file_count": self.file_count,
            "files": [
                {
                    "id": user_file.id,
                    "label": posixpath.basename(str(user_file.original_filename or "").strip()) or str(user_file.original_filename or "").strip(),
                    "path": str(user_file.original_filename or "").strip(),
                    "mime_type": str(user_file.mime_type or "").strip(),
                    "size": int(user_file.size or 0),
                    "scope": str(user_file.scope or "").strip(),
                }
                for user_file in self.files
            ],
            "has_untracked_files": bool(self.has_untracked_files),
        }


@dataclass(slots=True)
class MessageTailDeletionResult:
    thread_id: int
    thread_mode: str
    anchor_message_id: int
    deleted_message_ids: list[int]
    deleted_file_ids: list[int]
    redirect_day: str | None

    @property
    def deleted_message_count(self) -> int:
        return len(self.deleted_message_ids)

    @property
    def deleted_file_count(self) -> int:
        return len(self.deleted_file_ids)

    def serialize(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "thread_mode": self.thread_mode,
            "anchor_message_id": self.anchor_message_id,
            "deleted_message_count": self.deleted_message_count,
            "deleted_message_ids": list(self.deleted_message_ids),
            "deleted_file_count": self.deleted_file_count,
            "deleted_file_ids": list(self.deleted_file_ids),
            "redirect_day": self.redirect_day,
        }


def _after_anchor_clause(anchor_message: Message) -> Q:
    return Q(created_at__gt=anchor_message.created_at) | (
        Q(created_at=anchor_message.created_at) & Q(id__gt=anchor_message.id)
    )


def _assert_anchor_ownership(anchor_message: Message, user) -> None:
    if anchor_message.user_id != user.id or anchor_message.thread.user_id != user.id:
        raise MessageTailDeletionError("Unauthorized message access.")


def _ensure_thread_has_no_running_tasks(thread: Thread, user) -> None:
    reconcile_stale_running_tasks(thread=thread, user=user)
    if Task.objects.filter(thread=thread, user=user, status=TaskStatus.RUNNING).exists():
        raise MessageTailDeletionError(
            "Cannot delete conversation messages while an agent run is still active."
        )


def _collect_tail_messages(anchor_message: Message) -> list[Message]:
    return list(
        Message.objects.filter(
            user=anchor_message.user,
            thread=anchor_message.thread,
        )
        .filter(_after_anchor_clause(anchor_message))
        .order_by("created_at", "id")
    )


def _collect_deletable_files(*, thread: Thread, user, message_ids: list[int]) -> list[UserFile]:
    if not message_ids:
        return []
    return list(
        UserFile.objects.filter(
            user=user,
            thread=thread,
            source_message_id__in=message_ids,
            scope__in=[UserFile.Scope.MESSAGE_ATTACHMENT, UserFile.Scope.THREAD_SHARED],
        ).order_by("original_filename", "id")
    )


def _collect_referenced_file_ids(messages: list[Message]) -> set[int]:
    file_ids: set[int] = set()
    for message in list(messages or []):
        internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
        for item in list(internal_data.get("file_ids") or []):
            try:
                file_ids.add(int(item))
            except (TypeError, ValueError):
                continue
    return file_ids


def build_message_tail_preview(anchor_message: Message, user) -> MessageTailDeletionPreview:
    _assert_anchor_ownership(anchor_message, user)
    _ensure_thread_has_no_running_tasks(anchor_message.thread, user)

    tail_messages = _collect_tail_messages(anchor_message)
    tail_message_ids = [message.id for message in tail_messages]
    files = _collect_deletable_files(
        thread=anchor_message.thread,
        user=user,
        message_ids=tail_message_ids,
    )
    tracked_file_ids = {user_file.id for user_file in files}
    referenced_file_ids = _collect_referenced_file_ids(tail_messages)
    has_untracked_files = bool(referenced_file_ids - tracked_file_ids)
    return MessageTailDeletionPreview(
        thread_id=anchor_message.thread_id,
        thread_mode=str(anchor_message.thread.mode or ""),
        anchor_message_id=anchor_message.id,
        message_ids=tail_message_ids,
        files=files,
        has_untracked_files=has_untracked_files,
    )


def _clear_compaction_state(thread: Thread) -> None:
    for session in AgentThreadSession.objects.filter(thread=thread):
        session_state = dict(session.session_state or {})
        changed = False
        for key in (
            SESSION_KEY_HISTORY_SUMMARY,
            SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
            SESSION_KEY_COMPACTED_AT,
        ):
            if key in session_state:
                session_state.pop(key, None)
                changed = True
        if changed:
            session.session_state = session_state
            session.save(update_fields=["session_state", "updated_at"])


def _cancel_pending_interactions(thread: Thread, user, *, message_ids: list[int]) -> None:
    if not message_ids:
        return
    interaction_ids = {
        interaction_id
        for interaction_id in Message.objects.filter(id__in=message_ids)
        .exclude(interaction_id__isnull=True)
        .values_list("interaction_id", flat=True)
        if interaction_id is not None
    }
    if not interaction_ids:
        return

    pending_interactions = list(
        Interaction.objects.select_related("task").filter(
            id__in=interaction_ids,
            thread=thread,
            task__user=user,
            status=InteractionStatus.PENDING,
        )
    )
    for interaction in pending_interactions:
        interaction.answer = "Canceled because later conversation messages were deleted."
        interaction.status = InteractionStatus.CANCELED
        interaction.save(update_fields=["answer", "status", "updated_at"])

        task = interaction.task
        if task.status == TaskStatus.AWAITING_INPUT:
            task.status = TaskStatus.FAILED
            task.result = "system_error: Pending interaction canceled because later conversation messages were deleted."
            task.save(update_fields=["status", "result", "updated_at"])


def _repair_continuous_dependencies(
    *,
    thread: Thread,
    user,
    anchor_message: Message,
    tail_messages: list[Message],
) -> tuple[str | None, int | None, int | None]:
    deleted_message_ids = [message.id for message in tail_messages]
    if not deleted_message_ids:
        anchor_day = get_day_label_for_user(user, when=anchor_message.created_at)
        return anchor_day.isoformat(), None, None

    anchor_day = get_day_label_for_user(user, when=anchor_message.created_at)
    anchor_segment = DaySegment.objects.filter(
        user=user,
        thread=thread,
        day_label=anchor_day,
    ).first()

    deleted_message_id_set = set(deleted_message_ids)
    deleted_chunks_qs = TranscriptChunk.objects.filter(
        user=user,
        thread=thread,
    ).filter(
        Q(start_message_id__in=deleted_message_ids) | Q(end_message_id__in=deleted_message_ids)
    )
    reindex_anchor_segment = bool(
        anchor_segment
        and deleted_chunks_qs.filter(day_segment_id=anchor_segment.id).exists()
    )
    deleted_chunks_qs.delete()

    DaySegment.objects.filter(
        user=user,
        thread=thread,
        starts_at_message_id__in=deleted_message_ids,
    ).delete()

    summarize_anchor_segment_id: int | None = None
    if anchor_segment:
        anchor_day_deleted = any(
            get_day_label_for_user(user, when=message.created_at) == anchor_day
            for message in tail_messages
        )
        summary_until_message_id = anchor_segment.summary_until_message_id
        should_clear_summary = False
        if summary_until_message_id in deleted_message_id_set:
            should_clear_summary = True
        elif summary_until_message_id is not None and summary_until_message_id > anchor_message.id:
            should_clear_summary = True
        elif anchor_segment.summary_markdown and summary_until_message_id is None and anchor_day_deleted:
            should_clear_summary = True

        if should_clear_summary:
            anchor_segment.summary_markdown = ""
            anchor_segment.summary_until_message = None
            anchor_segment.save(update_fields=["summary_markdown", "summary_until_message", "updated_at"])
            DaySegmentEmbedding.objects.filter(day_segment=anchor_segment).delete()
            summarize_anchor_segment_id = anchor_segment.id

    redirect_day = (
        anchor_day.isoformat()
        if DaySegment.objects.filter(user=user, thread=thread, day_label=anchor_day).exists()
        else None
    )
    if redirect_day is None:
        latest_segment = DaySegment.objects.filter(user=user, thread=thread).order_by("-day_label").first()
        redirect_day = latest_segment.day_label.isoformat() if latest_segment else None

    return (
        redirect_day,
        anchor_segment.id if (anchor_segment and reindex_anchor_segment) else None,
        summarize_anchor_segment_id,
    )


def delete_message_tail_after(anchor_message: Message, user) -> MessageTailDeletionResult:
    _assert_anchor_ownership(anchor_message, user)
    _ensure_thread_has_no_running_tasks(anchor_message.thread, user)

    preview = build_message_tail_preview(anchor_message, user)
    deleted_message_ids = list(preview.message_ids)
    deleted_file_ids = [user_file.id for user_file in preview.files]
    redirect_day: str | None = None
    reindex_anchor_segment_id: int | None = None
    summarize_anchor_segment_id: int | None = None

    with transaction.atomic():
        tail_messages = _collect_tail_messages(anchor_message)
        deleted_message_ids = [message.id for message in tail_messages]
        if not deleted_message_ids:
            if anchor_message.thread.mode == Thread.Mode.CONTINUOUS:
                redirect_day = get_day_label_for_user(user, when=anchor_message.created_at).isoformat()
            return MessageTailDeletionResult(
                thread_id=anchor_message.thread_id,
                thread_mode=str(anchor_message.thread.mode or ""),
                anchor_message_id=anchor_message.id,
                deleted_message_ids=[],
                deleted_file_ids=[],
                redirect_day=redirect_day,
            )

        files_to_delete = _collect_deletable_files(
            thread=anchor_message.thread,
            user=user,
            message_ids=deleted_message_ids,
        )
        deleted_file_ids = [user_file.id for user_file in files_to_delete]

        _cancel_pending_interactions(
            anchor_message.thread,
            user,
            message_ids=deleted_message_ids,
        )

        if anchor_message.thread.mode == Thread.Mode.CONTINUOUS:
            (
                redirect_day,
                reindex_anchor_segment_id,
                summarize_anchor_segment_id,
            ) = _repair_continuous_dependencies(
                thread=anchor_message.thread,
                user=user,
                anchor_message=anchor_message,
                tail_messages=tail_messages,
            )

        _clear_compaction_state(anchor_message.thread)

        for user_file in files_to_delete:
            user_file.delete()

        Message.objects.filter(id__in=deleted_message_ids).delete()

        if reindex_anchor_segment_id or summarize_anchor_segment_id:
            def _enqueue_followups():
                if reindex_anchor_segment_id:
                    try:
                        index_transcript_append_task.delay(reindex_anchor_segment_id)
                    except Exception:
                        pass
                if summarize_anchor_segment_id:
                    try:
                        summarize_day_segment_task.delay(summarize_anchor_segment_id, mode="manual")
                    except Exception:
                        pass

            transaction.on_commit(_enqueue_followups)

        if any(user_file.scope == UserFile.Scope.THREAD_SHARED for user_file in files_to_delete):
            transaction.on_commit(
                lambda: async_to_sync(publish_file_update)(
                    anchor_message.thread_id,
                    "message_tail_delete",
                )
            )

    return MessageTailDeletionResult(
        thread_id=anchor_message.thread_id,
        thread_mode=str(anchor_message.thread.mode or ""),
        anchor_message_id=anchor_message.id,
        deleted_message_ids=deleted_message_ids,
        deleted_file_ids=deleted_file_ids,
        redirect_day=redirect_day,
    )
