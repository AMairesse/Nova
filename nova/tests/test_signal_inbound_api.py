from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase
from rest_framework.authtoken.models import Token

from nova.models.Message import Message


User = get_user_model()


class SignalInboundApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="signal-user", password="pass")
        self.token = Token.objects.create(user=self.user)
        self.url = reverse("signal-inbound")

    @patch("nova.continuous.message_ingest.enqueue_continuous_followups")
    @patch("nova.api.views.run_ai_task_celery.delay")
    def test_signal_inbound_ingests_message_into_continuous(self, mocked_run_ai, mocked_followups):
        response = self.client.post(
            self.url,
            data={"message": "hello from signal", "transport": "signal_cli", "external_message_id": "sig-msg-42"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "OK")
        self.assertIn("thread_id", payload)
        self.assertIn("task_id", payload)

        msg = Message.objects.get(id=payload["message_id"], user=self.user)
        self.assertEqual(msg.text, "hello from signal")
        self.assertEqual(msg.internal_data.get("source", {}).get("channel"), "signal")
        self.assertEqual(msg.internal_data.get("source", {}).get("transport"), "signal_cli")
        self.assertEqual(msg.internal_data.get("source", {}).get("external_message_id"), "sig-msg-42")

        mocked_run_ai.assert_called_once()
        mocked_followups.assert_called_once()

    def test_signal_inbound_requires_token_auth(self):
        response = self.client.post(
            self.url,
            data={"message": "hello"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_signal_inbound_validates_payload(self):
        response = self.client.post(
            self.url,
            data={"message": ""},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.json())

    def test_signal_inbound_rejects_unknown_selected_agent(self):
        response = self.client.post(
            self.url,
            data={"message": "hello", "selected_agent_id": 999999},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("selected_agent_id", response.json())
