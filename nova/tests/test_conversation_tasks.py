from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from nova.models.ConversationEmbedding import DaySegmentEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.models.UserObjects import UserProfile
from nova.tasks import conversation_tasks
from nova.tests.factories import create_agent, create_provider, create_user


class ConversationTasksFormattingTests(SimpleTestCase):
    def test_format_messages_for_summary_filters_and_truncates(self):
        long_text = "x" * 1700
        messages = [
            SimpleNamespace(actor=Actor.SYSTEM, text="hidden"),
            SimpleNamespace(actor=Actor.USER, text=" hello "),
            SimpleNamespace(actor=Actor.AGENT, text=long_text),
        ]

        transcript = conversation_tasks._format_messages_for_summary(messages)

        self.assertIn("User: hello", transcript)
        self.assertIn("Agent: ", transcript)
        self.assertIn("(truncated)", transcript)
        self.assertNotIn("hidden", transcript)


class ConversationTasksDbTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="conv-task-user", email="conv-task@example.com")
        self.provider = create_provider(self.user, name="conv-provider")
        self.agent = create_agent(self.user, self.provider, name="conv-agent")
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )

    def _create_segment(self, *, day_offset: int = 0, summary: str = "", with_default_agent: bool = False):
        start = self.thread.add_message("start", actor=Actor.USER)
        segment = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=(timezone.now() + timedelta(days=day_offset)).date(),
            starts_at_message=start,
            summary_markdown=summary,
        )
        if with_default_agent:
            UserProfile.objects.update_or_create(
                user=self.user,
                defaults={"default_agent": self.agent},
            )
        return segment, start

    def test_daysegment_needs_nightly_refresh_rules(self):
        seg, start = self._create_segment(summary="")
        self.assertTrue(conversation_tasks._daysegment_needs_nightly_refresh(seg))

        seg.summary_markdown = "Done"
        seg.summary_until_message = None
        seg.save(update_fields=["summary_markdown", "summary_until_message", "updated_at"])
        self.assertTrue(conversation_tasks._daysegment_needs_nightly_refresh(seg))

        boundary = self.thread.add_message("boundary", actor=Actor.AGENT)
        seg.summary_until_message = boundary
        seg.save(update_fields=["summary_until_message", "updated_at"])
        self.assertFalse(conversation_tasks._daysegment_needs_nightly_refresh(seg))

        self.thread.add_message("new info", actor=Actor.USER)
        self.assertTrue(conversation_tasks._daysegment_needs_nightly_refresh(seg))

    def test_daysegment_needs_nightly_refresh_respects_next_segment_boundary(self):
        seg1, _ = self._create_segment(day_offset=-2, summary="Summary")
        m2 = self.thread.add_message("seg1-last", actor=Actor.AGENT)
        seg1.summary_until_message = m2
        seg1.save(update_fields=["summary_until_message", "updated_at"])

        m3 = self.thread.add_message("seg2-start", actor=Actor.USER)
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=(timezone.now() + timedelta(days=-1)).date(),
            starts_at_message=m3,
            summary_markdown="",
        )
        self.thread.add_message("seg2-after", actor=Actor.AGENT)

        self.assertFalse(conversation_tasks._daysegment_needs_nightly_refresh(seg1))

    @patch("nova.tasks.conversation_tasks._publish_task_update", new_callable=AsyncMock)
    def test_summarize_day_segment_async_not_found(self, mocked_publish):
        result = asyncio.run(
            conversation_tasks._summarize_day_segment_async(
                day_segment_id=999999,
                mode="manual",
                task_id="task-1",
            )
        )

        self.assertEqual(result["status"], "not_found")
        self.assertTrue(any(call.args[1] == "task_error" for call in mocked_publish.await_args_list))

    @patch("nova.tasks.conversation_tasks._publish_task_update", new_callable=AsyncMock)
    def test_summarize_day_segment_async_no_default_agent(self, mocked_publish):
        seg, _ = self._create_segment(summary="")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = None
        profile.save(update_fields=["default_agent"])

        result = asyncio.run(
            conversation_tasks._summarize_day_segment_async(
                day_segment_id=seg.id,
                mode="manual",
                task_id="task-2",
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "no_default_agent")
        self.assertTrue(any(call.args[1] == "task_error" for call in mocked_publish.await_args_list))

    @patch("nova.tasks.conversation_tasks.LLMAgent.create", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_tasks._publish_task_update", new_callable=AsyncMock)
    def test_summarize_day_segment_async_empty_transcript_short_circuits(self, mocked_publish, mocked_create_agent):
        start = self.thread.add_message("system only", actor=Actor.SYSTEM)
        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=timezone.now().date(),
            starts_at_message=start,
            summary_markdown="",
        )
        UserProfile.objects.update_or_create(user=self.user, defaults={"default_agent": self.agent})

        result = asyncio.run(
            conversation_tasks._summarize_day_segment_async(
                day_segment_id=seg.id,
                mode="manual",
                task_id="task-3",
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"], "")
        mocked_create_agent.assert_not_awaited()
        self.assertTrue(any(call.args[1] == "task_complete" for call in mocked_publish.await_args_list))

    @patch("nova.tasks.conversation_tasks.compute_day_segment_embedding_task.delay")
    @patch("nova.tasks.conversation_tasks.LLMAgent.create", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_tasks._publish_task_update", new_callable=AsyncMock)
    def test_summarize_day_segment_async_persists_summary(self, mocked_publish, mocked_create_agent, mocked_delay):
        m1 = self.thread.add_message("Yesterday plan", actor=Actor.USER)
        m2 = self.thread.add_message("Today's action", actor=Actor.AGENT)
        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=timezone.now().date(),
            starts_at_message=m1,
            summary_markdown="",
        )
        UserProfile.objects.update_or_create(user=self.user, defaults={"default_agent": self.agent})

        fake_checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
        fake_agent = SimpleNamespace(
            ainvoke=AsyncMock(return_value="[THINK]internal[/THINK]\n## Summary\nAll good"),
            checkpointer=fake_checkpointer,
            cleanup=AsyncMock(),
        )
        mocked_create_agent.return_value = fake_agent

        result = asyncio.run(
            conversation_tasks._summarize_day_segment_async(
                day_segment_id=seg.id,
                mode="manual",
                task_id="task-4",
            )
        )

        self.assertEqual(result["status"], "ok")
        seg.refresh_from_db()
        self.assertIn("## Summary", seg.summary_markdown)
        self.assertNotIn("[THINK]", seg.summary_markdown)
        self.assertEqual(seg.summary_until_message_id, m2.id)
        emb = DaySegmentEmbedding.objects.get(day_segment=seg)
        self.assertEqual(emb.state, "pending")
        mocked_delay.assert_called_once_with(emb.id)
        fake_agent.ainvoke.assert_awaited_once_with(
            ANY,
            silent_mode=True,
            thread_id_override=ANY,
        )
        fake_checkpointer.adelete_thread.assert_awaited_once()
        fake_agent.cleanup.assert_awaited_once()
        self.assertTrue(any(call.args[1] == "continuous_summary_ready" for call in mocked_publish.await_args_list))

    @patch("nova.tasks.conversation_tasks.compute_day_segment_embedding_task.delay")
    @patch("nova.tasks.conversation_tasks.LLMAgent.create", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_tasks._publish_task_update", new_callable=AsyncMock)
    def test_manual_regenerate_ignores_delta_boundary(self, mocked_publish, mocked_create_agent, mocked_delay):
        m1 = self.thread.add_message("Initial context", actor=Actor.USER)
        m2 = self.thread.add_message("Initial answer", actor=Actor.AGENT)
        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=timezone.now().date(),
            starts_at_message=m1,
            summary_markdown="Old summary",
            summary_until_message=m2,
        )
        UserProfile.objects.update_or_create(user=self.user, defaults={"default_agent": self.agent})

        fake_checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
        fake_agent = SimpleNamespace(
            ainvoke=AsyncMock(return_value="## Summary\nRegenerated"),
            checkpointer=fake_checkpointer,
            cleanup=AsyncMock(),
        )
        mocked_create_agent.return_value = fake_agent

        result = asyncio.run(
            conversation_tasks._summarize_day_segment_async(
                day_segment_id=seg.id,
                mode="manual",
                task_id="task-manual-regenerate",
            )
        )

        self.assertEqual(result["status"], "ok")
        fake_agent.ainvoke.assert_awaited_once()
        prompt = fake_agent.ainvoke.await_args.args[0]
        self.assertIn("Messages for this day:", prompt)
        self.assertNotIn("New messages since the previous summary for this day", prompt)
        seg.refresh_from_db()
        self.assertEqual(seg.summary_markdown, "## Summary\nRegenerated")
        self.assertEqual(seg.summary_until_message_id, m2.id)
        emb = DaySegmentEmbedding.objects.get(day_segment=seg)
        mocked_delay.assert_called_once_with(emb.id)
        self.assertTrue(any(call.args[1] == "task_complete" for call in mocked_publish.await_args_list))

    @patch("nova.tasks.conversation_tasks.summarize_day_segment_task.delay")
    @patch("nova.tasks.conversation_tasks._daysegment_needs_nightly_refresh")
    def test_nightly_task_queues_only_needed_segments(self, mocked_needs_refresh, mocked_delay):
        seg1, _ = self._create_segment(day_offset=-2, summary="")
        seg2, _ = self._create_segment(day_offset=-1, summary="")
        mocked_needs_refresh.side_effect = [True, False]

        result = conversation_tasks.nightly_summarize_continuous_daysegments_task.run()

        self.assertEqual(result, {"status": "ok", "queued": 1})
        mocked_delay.assert_called_once_with(seg1.id, mode="nightly")
        self.assertEqual(mocked_needs_refresh.call_count, 2)
        self.assertNotEqual(seg1.id, seg2.id)

    @patch("nova.tasks.conversation_tasks._summarize_day_segment_async", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_tasks._daysegment_needs_nightly_refresh")
    def test_nightly_task_for_user_runs_sequential_updates(self, mocked_needs_refresh, mocked_summarize):
        seg1, _ = self._create_segment(day_offset=-2, summary="")
        self._create_segment(day_offset=-1, summary="")
        other_user = create_user(username="conv-other", email="conv-other@example.com")
        other_thread = Thread.objects.create(user=other_user, subject="Other", mode=Thread.Mode.CONTINUOUS)
        other_start = other_thread.add_message("other start", actor=Actor.USER)
        DaySegment.objects.create(
            user=other_user,
            thread=other_thread,
            day_label=(timezone.now() + timedelta(days=-1)).date(),
            starts_at_message=other_start,
            summary_markdown="",
        )
        mocked_needs_refresh.side_effect = [True, False]

        result = conversation_tasks.nightly_summarize_continuous_daysegments_for_user_task.run(self.user.id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["updated"], 1)
        mocked_summarize.assert_awaited_once_with(day_segment_id=seg1.id, mode="nightly")
