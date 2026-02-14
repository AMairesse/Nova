from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.continuous.message_ingest import ingest_continuous_user_message
from nova.models.Message import Message


User = get_user_model()


class ContinuousMessageIngestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ingest-user", password="pass")

    @patch("nova.continuous.message_ingest.enqueue_continuous_followups")
    def test_ingest_persists_source_metadata_for_non_web_channel(self, mocked_followups):
        fake_runner = SimpleNamespace(delay=lambda *args, **kwargs: None)

        result = ingest_continuous_user_message(
            user=self.user,
            message_text="Hello from Signal",
            run_ai_task=fake_runner,
            source_channel="signal",
            source_transport="signal_cli",
            source_external_message_id="sig-123",
        )

        message = Message.objects.get(id=result.message_id)
        self.assertEqual(message.internal_data.get("source", {}).get("channel"), "signal")
        self.assertEqual(message.internal_data.get("source", {}).get("transport"), "signal_cli")
        self.assertEqual(message.internal_data.get("source", {}).get("external_message_id"), "sig-123")
        mocked_followups.assert_called_once()
