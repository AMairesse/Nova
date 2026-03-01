import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from nova.models.PushSubscription import PushSubscription
from nova.models.UserObjects import UserParameters

User = get_user_model()


class PushViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="push-user", password="pass")
        self.other = User.objects.create_user(username="push-other", password="pass")
        self.client.login(username="push-user", password="pass")

    def test_push_config_disabled_by_default(self):
        response = self.client.get(reverse("push_config"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["server_enabled"])
        self.assertFalse(payload["server_configured"])
        self.assertEqual(payload["server_state"], "disabled")
        self.assertIsNone(payload["vapid_public_key"])
        self.assertFalse(payload["user_opt_in"])

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="",
        WEBPUSH_VAPID_PRIVATE_KEY="",
        WEBPUSH_VAPID_SUBJECT="",
    )
    def test_push_config_misconfigured_when_enabled_without_keys(self):
        response = self.client.get(reverse("push_config"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["server_enabled"])
        self.assertFalse(payload["server_configured"])
        self.assertEqual(payload["server_state"], "misconfigured")

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_push_subscription_upsert_and_delete_for_owner(self):
        payload = {
            "endpoint": "https://example.invalid/push/123",
            "expirationTime": None,
            "keys": {"p256dh": "pkey", "auth": "akey"},
        }
        response = self.client.post(
            reverse("push_subscriptions"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        sub = PushSubscription.objects.get(endpoint=payload["endpoint"])
        self.assertEqual(sub.user_id, self.user.id)
        self.assertTrue(sub.is_active)

        delete_response = self.client.delete(
            reverse("push_subscriptions"),
            data=json.dumps({"endpoint": payload["endpoint"]}),
            content_type="application/json",
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["status"], "ok")
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_push_subscription_delete_is_tenant_isolated(self):
        endpoint = "https://example.invalid/push/abc"
        PushSubscription.objects.create(
            user=self.other,
            endpoint=endpoint,
            p256dh="k1",
            auth="k2",
            is_active=True,
        )

        response = self.client.delete(
            reverse("push_subscriptions"),
            data=json.dumps({"endpoint": endpoint}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        sub = PushSubscription.objects.get(endpoint=endpoint)
        self.assertTrue(sub.is_active)

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_push_subscription_upsert_is_tenant_isolated(self):
        endpoint = "https://example.invalid/push/reassign"
        PushSubscription.objects.create(
            user=self.other,
            endpoint=endpoint,
            p256dh="other_p",
            auth="other_a",
            is_active=True,
        )

        payload = {
            "endpoint": endpoint,
            "expirationTime": None,
            "keys": {"p256dh": "mine_p", "auth": "mine_a"},
        }
        response = self.client.post(
            reverse("push_subscriptions"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "not_found")

        sub = PushSubscription.objects.get(endpoint=endpoint)
        self.assertEqual(sub.user_id, self.other.id)
        self.assertEqual(sub.p256dh, "other_p")
        self.assertEqual(sub.auth, "other_a")

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="",
        WEBPUSH_VAPID_PRIVATE_KEY="",
        WEBPUSH_VAPID_SUBJECT="",
    )
    def test_push_subscription_upsert_rejects_when_server_not_ready(self):
        payload = {
            "endpoint": "https://example.invalid/push/123",
            "keys": {"p256dh": "pkey", "auth": "akey"},
        }
        response = self.client.post(
            reverse("push_subscriptions"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "server_not_ready")
        self.assertEqual(PushSubscription.objects.count(), 0)

    @override_settings(
        WEBPUSH_ENABLED=True,
        WEBPUSH_VAPID_PUBLIC_KEY="pub",
        WEBPUSH_VAPID_PRIVATE_KEY="priv",
        WEBPUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    def test_push_config_reflects_user_opt_in(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.task_notifications_enabled = True
        params.save(update_fields=["task_notifications_enabled"])

        response = self.client.get(reverse("push_config"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["server_state"], "ready")
        self.assertEqual(payload["vapid_public_key"], "pub")
        self.assertTrue(payload["user_opt_in"])
