from types import SimpleNamespace
from unittest.mock import patch
import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user, get_or_create_day_segment
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message


User = get_user_model()


class ContinuousViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cont-user", password="pass")
        self.client.login(username="cont-user", password="pass")

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

    def test_continuous_home_invalid_day_falls_back_to_today(self):
        response = self.client.get(reverse("continuous_home"), data={"day": "invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["day_label"], get_day_label_for_user(self.user))

    def test_continuous_days_clamps_limit_to_100(self):
        thread = ensure_continuous_thread(self.user)
        today = get_day_label_for_user(self.user)
        # Build >100 segments to verify clamping.
        for i in range(110):
            label = today - dt.timedelta(days=i)
            msg = Message.objects.create(user=self.user, thread=thread, text=f"msg {i}", actor=Actor.USER)
            DaySegment.objects.get_or_create(
                user=self.user,
                thread=thread,
                day_label=label,
                defaults={"starts_at_message": msg, "summary_markdown": ""},
            )

        response = self.client.get(reverse("continuous_days"), data={"offset": 0, "limit": 500})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 100)
