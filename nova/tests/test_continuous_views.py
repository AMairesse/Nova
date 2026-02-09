from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user, get_or_create_day_segment
from nova.models.Message import Actor


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

    @patch("nova.views.continuous_views.summarize_day_segment_task.delay")
    @patch("nova.views.continuous_views.index_transcript_append_task.delay")
    @patch("nova.views.continuous_views.run_ai_task_celery.delay")
    def test_continuous_add_message_reports_opened_new_day(
        self,
        mocked_run_ai_task,
        mocked_index_task,
        mocked_summary_task,
    ):
        mocked_run_ai_task.return_value = None
        mocked_index_task.return_value = None
        mocked_summary_task.return_value = None

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

