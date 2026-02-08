import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase

from langchain_core.messages import SystemMessage

from nova.continuous.context_builder import load_continuous_context
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread


User = get_user_model()


class ContinuousContextBuilderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ctx-user",
            email="ctx-user@example.com",
            password="testpass123",
        )
        self.thread = Thread.objects.create(user=self.user, subject="Continuous", mode=Thread.Mode.CONTINUOUS)

    def _mk_msg(self, text: str):
        return Message.objects.create(user=self.user, thread=self.thread, actor=Actor.USER, text=text)

    def test_injects_previous_two_day_summaries_as_system_messages(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()
        d1 = today - dt.timedelta(days=1)
        d2 = today - dt.timedelta(days=2)

        m2 = self._mk_msg("m2")
        m1 = self._mk_msg("m1")

        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d2,
            starts_at_message=m2,
            summary_markdown="Summary for J-2",
        )
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d1,
            starts_at_message=m1,
            summary_markdown="Summary for J-1",
        )

        _, messages = load_continuous_context(self.user, self.thread)

        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        self.assertGreaterEqual(len(sys_msgs), 2)
        joined = "\n".join(str(m.content) for m in sys_msgs)
        self.assertIn(f"Summary of {d1.isoformat()}", joined)
        self.assertIn(f"Summary of {d2.isoformat()}", joined)

    def test_uses_two_latest_available_summaries_not_strict_calendar_days(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()
        d_recent = today - dt.timedelta(days=1)
        d_old_1 = today - dt.timedelta(days=4)
        d_old_2 = today - dt.timedelta(days=8)

        m_a = self._mk_msg("m-a")
        m_b = self._mk_msg("m-b")
        m_c = self._mk_msg("m-c")

        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d_old_2,
            starts_at_message=m_a,
            summary_markdown="Summary old 2",
        )
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d_old_1,
            starts_at_message=m_b,
            summary_markdown="Summary old 1",
        )
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d_recent,
            starts_at_message=m_c,
            summary_markdown="Summary recent",
        )

        _, messages = load_continuous_context(self.user, self.thread)

        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        joined = "\n".join(str(m.content) for m in sys_msgs)

        # Keep the two most recent summarized days prior to today.
        self.assertIn(f"Summary of {d_recent.isoformat()}", joined)
        self.assertIn(f"Summary of {d_old_1.isoformat()}", joined)
        self.assertNotIn(f"Summary of {d_old_2.isoformat()}", joined)

    def test_adds_fallback_notice_when_previous_summaries_are_truncated(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()
        d1 = today - dt.timedelta(days=1)
        d2 = today - dt.timedelta(days=2)

        m2 = self._mk_msg("m2")
        m1 = self._mk_msg("m1")

        huge = "verylong " * 10000
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d2,
            starts_at_message=m2,
            summary_markdown=huge,
        )
        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=d1,
            starts_at_message=m1,
            summary_markdown=huge,
        )

        _, messages = load_continuous_context(self.user, self.thread)

        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        joined = "\n".join(str(m.content) for m in sys_msgs)
        self.assertIn("conversation_search", joined)
        self.assertIn("conversation_get", joined)

    def test_injects_today_summary_and_filters_messages_after_boundary(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()

        m1 = self._mk_msg("today-before-1")
        m2 = self._mk_msg("today-before-2")
        self._mk_msg("today-after")

        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=today,
            starts_at_message=m1,
            summary_markdown="Today summary",
            summary_until_message=m2,
        )

        _, messages = load_continuous_context(self.user, self.thread)

        rendered = [str(m.content) for m in messages]

        # Today summary is injected.
        self.assertTrue(any(f"Summary of {today.isoformat()}" in c for c in rendered))

        # Messages at/before boundary are excluded.
        self.assertFalse(any("today-before-1" in c for c in rendered))
        self.assertFalse(any("today-before-2" in c for c in rendered))
        self.assertTrue(any("today-after" in c for c in rendered))

    def test_does_not_apply_boundary_when_today_summary_is_empty(self):
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date()

        m1 = self._mk_msg("today-before-1")
        m2 = self._mk_msg("today-before-2")
        self._mk_msg("today-after")

        DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=today,
            starts_at_message=m1,
            summary_markdown="   ",
            summary_until_message=m2,
        )

        _, messages = load_continuous_context(self.user, self.thread)

        rendered = [str(m.content) for m in messages]

        # No today summary injected when empty.
        self.assertFalse(any(f"Summary of {today.isoformat()}" in c for c in rendered))

        # Boundary is not applied without summary content.
        self.assertTrue(any("today-before-1" in c for c in rendered))
        self.assertTrue(any("today-before-2" in c for c in rendered))
        self.assertTrue(any("today-after" in c for c in rendered))
