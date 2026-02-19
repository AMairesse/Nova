from types import SimpleNamespace
from unittest.mock import patch
import datetime as dt
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user, get_or_create_day_segment
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Actor, Message
from nova.models.Task import Task, TaskStatus
from nova.tests.factories import create_agent, create_provider


User = get_user_model()


class ContinuousViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cont-user", password="pass")
        self.client.login(username="cont-user", password="pass")

    def _create_day_segments(self, total_days: int):
        thread = ensure_continuous_thread(self.user)
        today = get_day_label_for_user(self.user)
        labels = []

        for i in range(total_days):
            label = today - dt.timedelta(days=i)
            msg = Message.objects.create(user=self.user, thread=thread, text=f"msg {i}", actor=Actor.USER)
            DaySegment.objects.get_or_create(
                user=self.user,
                thread=thread,
                day_label=label,
                defaults={"starts_at_message": msg, "summary_markdown": ""},
            )
            labels.append(label.isoformat())

        return thread, labels

    def _extract_day_labels_from_html(self, html: str):
        return re.findall(r'data-day-label="(\d{4}-\d{2}-\d{2})"', html or "")

    @patch("nova.views.continuous_views.summarize_day_segment_task.delay")
    def test_continuous_regenerate_summary_returns_task_id(self, mocked_delay):
        mocked_delay.return_value = SimpleNamespace(id="summary-task-123")

        thread = ensure_continuous_thread(self.user)
        day_label = get_day_label_for_user(self.user)
        msg = thread.add_message("Hello", actor=Actor.USER)
        get_or_create_day_segment(self.user, thread, day_label, starts_at_message=msg)

        response = self.client.post(
            reverse("continuous_regenerate_summary"),
            data={"day": day_label.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["day_label"], day_label.isoformat())
        self.assertEqual(payload["task_id"], "summary-task-123")

    @patch("nova.views.continuous_views.enqueue_continuous_followups")
    @patch("nova.views.continuous_views.run_ai_task_celery.delay")
    def test_continuous_add_message_reports_opened_new_day(
        self,
        mocked_run_ai_task,
        mocked_enqueue_followups,
    ):
        mocked_run_ai_task.return_value = None
        mocked_enqueue_followups.return_value = None

        response_1 = self.client.post(
            reverse("continuous_add_message"),
            data={"new_message": "First message"},
        )
        self.assertEqual(response_1.status_code, 200)
        payload_1 = response_1.json()
        self.assertEqual(payload_1["status"], "OK")
        self.assertTrue(payload_1["opened_new_day"])
        self.assertEqual(payload_1["day_label"], get_day_label_for_user(self.user).isoformat())

        response_2 = self.client.post(
            reverse("continuous_add_message"),
            data={"new_message": "Second message"},
        )
        self.assertEqual(response_2.status_code, 200)
        payload_2 = response_2.json()
        self.assertEqual(payload_2["status"], "OK")
        self.assertFalse(payload_2["opened_new_day"])
        self.assertEqual(payload_2["day_label"], get_day_label_for_user(self.user).isoformat())

    def test_continuous_regenerate_summary_returns_404_when_segment_missing(self):
        response = self.client.post(reverse("continuous_regenerate_summary"), data={"day": "2026-01-01"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "no_day_segment")

    def test_continuous_day_rejects_invalid_date(self):
        response = self.client.get(reverse("continuous_day", args=["invalid-date"]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_day")

    def test_continuous_messages_rejects_invalid_day_query(self):
        response = self.client.get(reverse("continuous_messages"), data={"day": "invalid-date"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_day")

    def test_continuous_messages_marks_past_day_read_only(self):
        thread = ensure_continuous_thread(self.user)
        today_label = get_day_label_for_user(self.user)
        yesterday = today_label - dt.timedelta(days=1)
        message = thread.add_message("Yesterday", actor=Actor.USER)
        get_or_create_day_segment(self.user, thread, yesterday, starts_at_message=message)
        # Ensure there is a segment for today so yesterday gets a proper end boundary.
        today_message = thread.add_message("Today", actor=Actor.USER)
        get_or_create_day_segment(self.user, thread, today_label, starts_at_message=today_message)

        response = self.client.get(reverse("continuous_messages"), data={"day": yesterday.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["allow_posting"], False)
        self.assertEqual(response.context["thread_id"], thread.id)

    def test_continuous_messages_returns_empty_when_no_day_segment(self):
        response = self.client.get(reverse("continuous_messages"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["messages"]), [])
        self.assertTrue(response.context["allow_posting"])

    def test_continuous_messages_default_view_shows_recent_history_without_day_segment(self):
        thread = ensure_continuous_thread(self.user)
        thread.add_message("First", actor=Actor.USER)
        thread.add_message("Second", actor=Actor.AGENT)

        response = self.client.get(reverse("continuous_messages"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.text for m in response.context["messages"]], ["First", "Second"])
        self.assertTrue(response.context["allow_posting"])
        self.assertTrue(response.context["is_continuous_default_mode"])

    def test_continuous_messages_default_view_respects_recent_limit_setting(self):
        thread = ensure_continuous_thread(self.user)
        self.user.userparameters.continuous_default_messages_limit = 2
        self.user.userparameters.save(update_fields=["continuous_default_messages_limit"])

        for idx in range(5):
            thread.add_message(f"msg-{idx}", actor=Actor.USER)

        response = self.client.get(reverse("continuous_messages"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.text for m in response.context["messages"]], ["msg-3", "msg-4"])
        self.assertEqual(response.context["recent_messages_limit"], 2)

    def test_continuous_home_invalid_day_falls_back_to_today(self):
        response = self.client.get(reverse("continuous_home"), data={"day": "invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["day_label"], get_day_label_for_user(self.user))

    def test_continuous_days_clamps_limit_to_100(self):
        self._create_day_segments(110)

        response = self.client.get(reverse("continuous_days"), data={"offset": 0, "limit": 500})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 100)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["next_offset"], 100)

    def test_continuous_days_returns_pagination_fields(self):
        self._create_day_segments(35)

        response = self.client.get(reverse("continuous_days"), data={"offset": 0, "limit": 30})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 30)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["next_offset"], 30)
        self.assertEqual(payload["applied_query"], "")

        response_2 = self.client.get(reverse("continuous_days"), data={"offset": 30, "limit": 30})
        self.assertEqual(response_2.status_code, 200)
        payload_2 = response_2.json()
        self.assertEqual(payload_2["count"], 5)
        self.assertFalse(payload_2["has_more"])
        self.assertIsNone(payload_2["next_offset"])

    def test_continuous_days_offset_pagination_has_no_duplicates(self):
        self._create_day_segments(45)

        page_1 = self.client.get(reverse("continuous_days"), data={"offset": 0, "limit": 20}).json()
        page_2 = self.client.get(reverse("continuous_days"), data={"offset": 20, "limit": 20}).json()

        labels_1 = set(self._extract_day_labels_from_html(page_1["html"]))
        labels_2 = set(self._extract_day_labels_from_html(page_2["html"]))
        self.assertEqual(len(labels_1), 20)
        self.assertEqual(len(labels_2), 20)
        self.assertEqual(labels_1.intersection(labels_2), set())

    def test_continuous_days_query_filters_by_year_month(self):
        thread = ensure_continuous_thread(self.user)
        target_days = [dt.date(2026, 2, 17), dt.date(2026, 2, 1), dt.date(2026, 1, 31)]

        for idx, day_label in enumerate(target_days):
            msg = Message.objects.create(user=self.user, thread=thread, text=f"qmsg {idx}", actor=Actor.USER)
            DaySegment.objects.get_or_create(
                user=self.user,
                thread=thread,
                day_label=day_label,
                defaults={"starts_at_message": msg, "summary_markdown": ""},
            )

        response = self.client.get(reverse("continuous_days"), data={"q": "2026-02", "offset": 0, "limit": 30})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        labels = set(self._extract_day_labels_from_html(payload["html"]))
        self.assertEqual(payload["applied_query"], "2026-02")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(labels, {"2026-02-17", "2026-02-01"})

    def test_continuous_days_query_filters_by_exact_date(self):
        thread = ensure_continuous_thread(self.user)
        msg_1 = Message.objects.create(user=self.user, thread=thread, text="exact 1", actor=Actor.USER)
        msg_2 = Message.objects.create(user=self.user, thread=thread, text="exact 2", actor=Actor.USER)
        DaySegment.objects.get_or_create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2026, 2, 17),
            defaults={"starts_at_message": msg_1, "summary_markdown": ""},
        )
        DaySegment.objects.get_or_create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2026, 2, 18),
            defaults={"starts_at_message": msg_2, "summary_markdown": ""},
        )

        response = self.client.get(reverse("continuous_days"), data={"q": "2026-02-17", "offset": 0, "limit": 30})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        labels = self._extract_day_labels_from_html(payload["html"])
        self.assertEqual(payload["applied_query"], "2026-02-17")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(labels, ["2026-02-17"])

    def test_continuous_days_query_filters_by_year(self):
        thread = ensure_continuous_thread(self.user)
        msg_1 = Message.objects.create(user=self.user, thread=thread, text="year 1", actor=Actor.USER)
        msg_2 = Message.objects.create(user=self.user, thread=thread, text="year 2", actor=Actor.USER)
        DaySegment.objects.get_or_create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2026, 12, 31),
            defaults={"starts_at_message": msg_1, "summary_markdown": ""},
        )
        DaySegment.objects.get_or_create(
            user=self.user,
            thread=thread,
            day_label=dt.date(2025, 12, 31),
            defaults={"starts_at_message": msg_2, "summary_markdown": ""},
        )

        response = self.client.get(reverse("continuous_days"), data={"q": "2026", "offset": 0, "limit": 30})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        labels = self._extract_day_labels_from_html(payload["html"])
        self.assertEqual(payload["applied_query"], "2026")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(labels, ["2026-12-31"])

    def test_continuous_home_exposes_interaction_urls(self):
        response = self.client.get(reverse("continuous_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-url-interaction-answer="')
        self.assertContains(response, 'data-url-interaction-cancel="')

    def test_continuous_home_exposes_days_sidebar_toggle_controls(self):
        response = self.client.get(reverse("continuous_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="continuous-days-toggle-btn"')
        self.assertContains(response, 'id="continuous-days-toggle-icon"')

    def test_continuous_messages_includes_pending_interactions(self):
        thread = ensure_continuous_thread(self.user)
        provider = create_provider(self.user, name="Continuous Provider")
        agent = create_agent(self.user, provider, name="Continuous Agent")
        task = Task.objects.create(
            user=self.user,
            thread=thread,
            agent_config=agent,
            status=TaskStatus.AWAITING_INPUT,
        )

        interaction = Interaction.objects.create(
            task=task,
            thread=thread,
            agent_config=agent,
            origin_name=agent.name,
            question="Which mailbox should I use?",
            schema={},
            status=InteractionStatus.PENDING,
        )

        response = self.client.get(reverse("continuous_messages"))

        self.assertEqual(response.status_code, 200)
        pending = list(response.context["pending_interactions"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, interaction.id)
