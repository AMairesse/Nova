from __future__ import annotations

import datetime as dt
import importlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nova.continuous.utils import ensure_continuous_thread
from nova.models.ConversationEmbedding import DaySegmentEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Actor, Message, MessageType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk
from nova.models.UserFile import UserFile
from nova.tests.factories import create_agent, create_provider

User = get_user_model()


async def _fake_publish_file_update(*_args, **_kwargs):
    return None


class MessageTailDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tail-user", password="pass")
        self.other = User.objects.create_user(username="tail-other", password="pass")
        self.client.login(username="tail-user", password="pass")

        self.delete_storage_patcher = patch(
            "nova.models.UserFile.UserFile.delete_storage_object",
            new=Mock(),
        )
        self.delete_storage_patcher.start()
        self.addCleanup(self.delete_storage_patcher.stop)
        provider = create_provider(self.user, name="Tail provider")
        self.agent = create_agent(self.user, provider, name="Tail agent")

    def _aware(self, year: int, month: int, day: int, hour: int = 10, minute: int = 0):
        return timezone.make_aware(dt.datetime(year, month, day, hour, minute))

    def _set_created_at(self, message: Message, value):
        Message.objects.filter(id=message.id).update(created_at=value)
        message.refresh_from_db()
        return message

    def _create_file(
        self,
        *,
        thread: Thread,
        path: str,
        scope: str,
        source_message: Message | None,
        user=None,
    ) -> UserFile:
        owner = user or self.user
        return UserFile.objects.create(
            user=owner,
            thread=thread,
            source_message=source_message,
            key=f"users/{owner.id}/threads/{thread.id}{path}",
            original_filename=path,
            mime_type="text/plain",
            size=128,
            scope=scope,
        )

    @patch("nova.message_tail_service.publish_file_update", new=_fake_publish_file_update)
    def test_preview_and_delete_tail_removes_later_messages_and_attributable_files(self):
        thread = Thread.objects.create(user=self.user, subject="Rollback thread")
        anchor = self._set_created_at(
            thread.add_message("Anchor", actor=Actor.USER),
            self._aware(2026, 4, 10, 10, 0),
        )
        later_1 = self._set_created_at(
            thread.add_message("Later 1", actor=Actor.AGENT),
            self._aware(2026, 4, 10, 10, 1),
        )
        later_2 = self._set_created_at(
            thread.add_message("Later 2", actor=Actor.USER),
            self._aware(2026, 4, 10, 10, 2),
        )

        tracked_thread_file = self._create_file(
            thread=thread,
            path="/generated/report.txt",
            scope=UserFile.Scope.THREAD_SHARED,
            source_message=later_1,
        )
        tracked_attachment = self._create_file(
            thread=thread,
            path="/photo/reference.png",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            source_message=later_2,
        )
        untracked_file = self._create_file(
            thread=thread,
            path="/keep/me.txt",
            scope=UserFile.Scope.THREAD_SHARED,
            source_message=None,
        )

        preview_response = self.client.get(
            reverse("preview_delete_message_tail", args=[anchor.id])
        )
        self.assertEqual(preview_response.status_code, 200)
        preview_payload = preview_response.json()
        self.assertEqual(preview_payload["status"], "OK")
        self.assertEqual(preview_payload["message_count"], 2)
        self.assertEqual(preview_payload["file_count"], 2)
        self.assertFalse(preview_payload["has_untracked_files"])
        self.assertEqual(
            {item["id"] for item in preview_payload["files"]},
            {tracked_thread_file.id, tracked_attachment.id},
        )

        delete_response = self.client.post(
            reverse("delete_message_tail", args=[anchor.id])
        )
        self.assertEqual(delete_response.status_code, 200)
        payload = delete_response.json()
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["deleted_message_count"], 2)
        self.assertEqual(payload["deleted_file_count"], 2)

        self.assertTrue(Message.objects.filter(id=anchor.id).exists())
        self.assertFalse(Message.objects.filter(id=later_1.id).exists())
        self.assertFalse(Message.objects.filter(id=later_2.id).exists())
        self.assertFalse(UserFile.objects.filter(id=tracked_thread_file.id).exists())
        self.assertFalse(UserFile.objects.filter(id=tracked_attachment.id).exists())
        self.assertTrue(UserFile.objects.filter(id=untracked_file.id).exists())

    def test_preview_reports_untracked_historical_files(self):
        thread = Thread.objects.create(user=self.user, subject="Rollback thread")
        anchor = self._set_created_at(
            thread.add_message("Anchor", actor=Actor.USER),
            self._aware(2026, 4, 10, 10, 0),
        )
        later = self._set_created_at(
            thread.add_message("Later", actor=Actor.AGENT),
            self._aware(2026, 4, 10, 10, 1),
        )
        historical_file = self._create_file(
            thread=thread,
            path="/legacy/note.txt",
            scope=UserFile.Scope.THREAD_SHARED,
            source_message=None,
        )
        later.internal_data = {"file_ids": [historical_file.id]}
        later.save(update_fields=["internal_data"])

        preview_response = self.client.get(
            reverse("preview_delete_message_tail", args=[anchor.id])
        )

        self.assertEqual(preview_response.status_code, 200)
        payload = preview_response.json()
        self.assertEqual(payload["file_count"], 0)
        self.assertTrue(payload["has_untracked_files"])

    def test_delete_tail_rejects_running_thread_tasks(self):
        thread = Thread.objects.create(user=self.user, subject="Busy thread")
        anchor = thread.add_message("Anchor", actor=Actor.USER)
        thread.add_message("Later", actor=Actor.AGENT)
        Task.objects.create(
            user=self.user,
            thread=thread,
            status=TaskStatus.RUNNING,
        )

        response = self.client.post(reverse("delete_message_tail", args=[anchor.id]))

        self.assertEqual(response.status_code, 400)
        self.assertIn("still active", response.json()["message"])

    def test_delete_tail_cancels_pending_interactions_in_deleted_tail(self):
        thread = Thread.objects.create(user=self.user, subject="Interaction thread")
        anchor = self._set_created_at(
            thread.add_message("Anchor", actor=Actor.USER),
            self._aware(2026, 4, 10, 10, 0),
        )
        task = Task.objects.create(
            user=self.user,
            thread=thread,
            status=TaskStatus.AWAITING_INPUT,
        )
        interaction = Interaction.objects.create(
            task=task,
            thread=thread,
            agent_config=self.agent,
            origin_name="Nova",
            question="Need more input",
            schema={},
            status=InteractionStatus.PENDING,
        )
        tail_message = Message.objects.create(
            user=self.user,
            thread=thread,
            actor=Actor.AGENT,
            text="Need more input",
            message_type=MessageType.INTERACTION_QUESTION,
            interaction=interaction,
        )
        self._set_created_at(tail_message, self._aware(2026, 4, 10, 10, 1))

        response = self.client.post(reverse("delete_message_tail", args=[anchor.id]))

        self.assertEqual(response.status_code, 200)
        interaction.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(interaction.status, InteractionStatus.CANCELED)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("Pending interaction canceled", task.result)

    @patch("nova.message_tail_service.publish_file_update", new=_fake_publish_file_update)
    @patch("nova.message_tail_service.summarize_day_segment_task.delay")
    @patch("nova.message_tail_service.index_transcript_append_task.delay")
    def test_delete_tail_repairs_continuous_state(
        self,
        mocked_reindex,
        mocked_summarize,
    ):
        thread = ensure_continuous_thread(self.user)
        anchor = self._set_created_at(
            thread.add_message("Day 1 start", actor=Actor.USER),
            self._aware(2026, 4, 10, 9, 0),
        )
        later_same_day = self._set_created_at(
            thread.add_message("Day 1 later", actor=Actor.AGENT),
            self._aware(2026, 4, 10, 9, 15),
        )
        later_next_day = self._set_created_at(
            thread.add_message("Day 2", actor=Actor.USER),
            self._aware(2026, 4, 11, 9, 0),
        )

        day_one = DaySegment.objects.create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2026, 4, 10),
            starts_at_message=anchor,
            summary_markdown="Summary",
            summary_until_message=later_same_day,
        )
        day_two = DaySegment.objects.create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2026, 4, 11),
            starts_at_message=later_next_day,
            summary_markdown="Summary 2",
        )
        DaySegmentEmbedding.objects.create(user=self.user, day_segment=day_one)
        TranscriptChunk.objects.create(
            user=self.user,
            thread=thread,
            day_segment=day_one,
            start_message=anchor,
            end_message=later_same_day,
            content_text="Day one transcript",
            content_hash="day-one",
            token_estimate=12,
        )
        TranscriptChunk.objects.create(
            user=self.user,
            thread=thread,
            day_segment=day_two,
            start_message=later_next_day,
            end_message=later_next_day,
            content_text="Day two transcript",
            content_hash="day-two",
            token_estimate=7,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("delete_message_tail", args=[anchor.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["redirect_day"], "2026-04-10")

        day_one.refresh_from_db()
        self.assertEqual(day_one.summary_markdown, "")
        self.assertIsNone(day_one.summary_until_message_id)
        self.assertFalse(DaySegment.objects.filter(id=day_two.id).exists())
        self.assertEqual(TranscriptChunk.objects.filter(thread=thread).count(), 0)
        self.assertFalse(DaySegmentEmbedding.objects.filter(day_segment=day_one).exists())
        mocked_reindex.assert_called_once_with(day_one.id)
        mocked_summarize.assert_called_once_with(day_one.id, mode="manual")

    def test_backfill_source_message_uses_internal_data_file_ids_when_unique(self):
        thread = Thread.objects.create(user=self.user, subject="Backfill thread")
        message = thread.add_message("Source", actor=Actor.USER)
        user_file = self._create_file(
            thread=thread,
            path="/notes/source.txt",
            scope=UserFile.Scope.THREAD_SHARED,
            source_message=None,
        )
        message.internal_data = {"file_ids": [user_file.id]}
        message.save(update_fields=["internal_data"])

        migration_module = importlib.import_module(
            "nova.migrations.0079_userfile_source_message_set_null"
        )
        migration_module.backfill_userfile_source_message(
            apps=SimpleNamespace(get_model=lambda app_label, model_name: {
                ("nova", "Message"): Message,
                ("nova", "UserFile"): UserFile,
            }[(app_label, model_name)]),
            schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )

        user_file.refresh_from_db()
        self.assertEqual(user_file.source_message_id, message.id)

    def test_backfill_source_message_skips_ambiguous_file_ids(self):
        thread = Thread.objects.create(user=self.user, subject="Backfill thread")
        message_one = thread.add_message("Source 1", actor=Actor.USER)
        message_two = thread.add_message("Source 2", actor=Actor.AGENT)
        user_file = self._create_file(
            thread=thread,
            path="/notes/source.txt",
            scope=UserFile.Scope.THREAD_SHARED,
            source_message=None,
        )
        message_one.internal_data = {"file_ids": [user_file.id]}
        message_one.save(update_fields=["internal_data"])
        message_two.internal_data = {"file_ids": [user_file.id]}
        message_two.save(update_fields=["internal_data"])

        migration_module = importlib.import_module(
            "nova.migrations.0079_userfile_source_message_set_null"
        )
        migration_module.backfill_userfile_source_message(
            apps=SimpleNamespace(get_model=lambda app_label, model_name: {
                ("nova", "Message"): Message,
                ("nova", "UserFile"): UserFile,
            }[(app_label, model_name)]),
            schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )

        user_file.refresh_from_db()
        self.assertIsNone(user_file.source_message_id)
