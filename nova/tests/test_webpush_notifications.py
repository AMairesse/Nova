from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from nova.models.PushSubscription import PushSubscription
from nova.models.UserObjects import UserParameters
from nova.notifications.webpush import send_task_notification_to_user

User = get_user_model()


class WebPushNotificationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="webpush-user", password="pass")
        self.params, _ = UserParameters.objects.get_or_create(user=self.user)

    def _create_subscription(self) -> PushSubscription:
        return PushSubscription.objects.create(
            user=self.user,
            endpoint="https://example.invalid/push/sub",
            p256dh="pkey",
            auth="akey",
            is_active=True,
        )

    def test_send_task_notification_skips_when_server_disabled(self):
        self.params.task_notifications_enabled = True
        self.params.save(update_fields=["task_notifications_enabled"])
        self._create_subscription()

        result = send_task_notification_to_user(
            user_id=self.user.id,
            task_id="task-1",
            thread_id=12,
            thread_mode="thread",
            status="completed",
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "server_disabled")

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_send_task_notification_skips_when_user_opt_out(self):
        self.params.task_notifications_enabled = False
        self.params.save(update_fields=["task_notifications_enabled"])
        self._create_subscription()

        result = send_task_notification_to_user(
            user_id=self.user.id,
            task_id="task-2",
            thread_id=12,
            thread_mode="thread",
            status="completed",
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "user_opt_out")

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_send_task_notification_sends_for_ready_server(self):
        self.params.task_notifications_enabled = True
        self.params.save(update_fields=["task_notifications_enabled"])
        sub = self._create_subscription()

        mocked_webpush = Mock()
        fake_module = SimpleNamespace(webpush=mocked_webpush)
        with patch.dict("sys.modules", {"pywebpush": fake_module}):
            result = send_task_notification_to_user(
                user_id=self.user.id,
                task_id="task-3",
                thread_id=42,
                thread_mode="thread",
                status="completed",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sent"], 1)
        mocked_webpush.assert_called_once()
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.last_error, "")
        self.assertIsNotNone(sub.last_success_at)

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_send_task_notification_disables_subscription_on_410(self):
        self.params.task_notifications_enabled = True
        self.params.save(update_fields=["task_notifications_enabled"])
        sub = self._create_subscription()

        class FakeWebPushError(Exception):
            def __init__(self):
                super().__init__("gone")
                self.response = SimpleNamespace(status_code=410)

        def fake_webpush(**kwargs):
            raise FakeWebPushError()

        fake_module = SimpleNamespace(webpush=fake_webpush)
        with patch.dict("sys.modules", {"pywebpush": fake_module}):
            result = send_task_notification_to_user(
                user_id=self.user.id,
                task_id="task-4",
                thread_id=77,
                thread_mode="thread",
                status="failed",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["invalidated"], 1)
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)
