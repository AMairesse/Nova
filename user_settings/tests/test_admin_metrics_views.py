from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric


User = get_user_model()


class AdminMetricsViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="staff-user",
            email="staff@example.com",
            password="pass123",
            is_staff=True,
        )
        self.normal_user = User.objects.create_user(
            username="normal-user",
            email="normal@example.com",
            password="pass123",
        )
        self.url = reverse("user_settings:admin-metrics")
        now = timezone.now()

        self.ls_metric = TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-06",
            runtime_engine="react_terminal_v1",
            head_command="ls",
            failure_kind="invalid_arguments",
            count=3,
            last_seen_at=now - timedelta(days=1),
            recent_examples=["ls -h"],
            last_error="Unsupported ls flag: -h",
        )
        self.unknown_metric = TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-06",
            runtime_engine="react_terminal_v1",
            head_command="unknowncmd",
            failure_kind="unknown_command",
            count=5,
            last_seen_at=now - timedelta(days=2),
            recent_examples=["unknowncmd --token <redacted>"],
            last_error="Unknown command: unknowncmd",
        )
        self.legacy_metric = TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-05",
            runtime_engine="legacy_terminal",
            head_command="pwd",
            failure_kind="unsupported_syntax",
            count=2,
            last_seen_at=now - timedelta(days=30),
            recent_examples=["pwd && ls"],
            last_error="Shell chaining with && and || is not supported.",
        )

    def _messages(self, response):
        return [message.message for message in get_messages(response.wsgi_request)]

    def test_login_required(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_non_staff_user_cannot_access_admin_metrics(self):
        self.client.login(username="normal-user", password="pass123")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_staff_user_can_view_admin_metrics_page(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/admin_metrics.html")
        self.assertContains(response, "Admin metrics")
        self.assertContains(response, "unknowncmd")
        self.assertContains(response, "legacy_terminal")
        self.assertContains(response, "Delete bucket")
        self.assertContains(response, 'data-bs-target="#purgeMetricsModal"', html=False)
        self.assertContains(response, 'id="purgeMetricsModal"', html=False)
        self.assertEqual(response.context["summary"]["total_events"], 10)
        self.assertEqual(response.context["summary"]["top_command"]["head_command"], "unknowncmd")
        self.assertEqual(response.context["displayed_rows_count"], 3)
        self.assertEqual(len(response.context["metrics_rows"]), 3)

    def test_admin_metrics_filters_queryset(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(
            self.url,
            {
                "q": "unknown",
                "runtime_engine": "react_terminal_v1",
                "failure_kind": "unknown_command",
            },
        )

        self.assertEqual(response.status_code, 200)
        metrics = list(response.context["metrics"])
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].head_command, "unknowncmd")
        self.assertEqual(response.context["summary"]["total_events"], 5)

    def test_delete_bucket_preserves_filters_and_shows_success_message(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.post(
            reverse("user_settings:admin-metrics-delete", args=[self.unknown_metric.pk]),
            {
                "confirm": "1",
                "q": "unknown",
                "runtime_engine": "react_terminal_v1",
                "failure_kind": "unknown_command",
            },
            follow=True,
        )

        self.assertEqual(response.redirect_chain[0][0], f"{self.url}?q=unknown&runtime_engine=react_terminal_v1&failure_kind=unknown_command")
        self.assertFalse(TerminalCommandFailureMetric.objects.filter(pk=self.unknown_metric.pk).exists())
        self.assertIn("Deleted metrics bucket:", self._messages(response)[0])

    def test_purge_matching_buckets_uses_current_filters(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.post(
            reverse("user_settings:admin-metrics-purge"),
            {
                "confirm": "1",
                "runtime_engine": "react_terminal_v1",
                "failure_kind": "",
                "q": "",
                "older_than_days": "",
            },
            follow=True,
        )

        self.assertEqual(response.redirect_chain[0][0], f"{self.url}?runtime_engine=react_terminal_v1")
        remaining_commands = set(
            TerminalCommandFailureMetric.objects.values_list("head_command", flat=True)
        )
        self.assertEqual(remaining_commands, {"pwd"})
        self.assertIn("Purged 2 metrics bucket(s).", self._messages(response))

    def test_purge_matching_buckets_can_target_only_old_rows(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.post(
            reverse("user_settings:admin-metrics-purge"),
            {
                "confirm": "1",
                "q": "",
                "runtime_engine": "",
                "failure_kind": "",
                "older_than_days": "7",
            },
            follow=True,
        )

        remaining_commands = set(
            TerminalCommandFailureMetric.objects.values_list("head_command", flat=True)
        )
        self.assertEqual(remaining_commands, {"ls", "unknowncmd"})
        self.assertIn("Purged 1 metrics bucket(s) older than 7 days.", self._messages(response))

    def test_purge_matching_buckets_reports_empty_selection(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.post(
            reverse("user_settings:admin-metrics-purge"),
            {
                "confirm": "1",
                "q": "no-match",
                "runtime_engine": "",
                "failure_kind": "",
                "older_than_days": "",
            },
            follow=True,
        )

        self.assertEqual(TerminalCommandFailureMetric.objects.count(), 3)
        self.assertIn("No metrics buckets matched the current cleanup selection.", self._messages(response))

    def test_dashboard_no_longer_shows_admin_tab(self):
        self.client.login(username="normal-user", password="pass123")
        normal_response = self.client.get(reverse("user_settings:dashboard"))
        self.assertEqual(normal_response.status_code, 200)
        self.assertNotContains(normal_response, 'data-bs-target="#pane-admin"', html=False)
        self.assertNotContains(normal_response, "Loading admin dashboard")

        self.client.login(username="staff-user", password="pass123")
        staff_response = self.client.get(reverse("user_settings:dashboard"))
        self.assertEqual(staff_response.status_code, 200)
        self.assertNotContains(staff_response, 'data-bs-target="#pane-admin"', html=False)
        self.assertNotContains(staff_response, "Loading admin dashboard")

    def test_staff_user_menu_exposes_admin_metrics_link(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_settings:admin-metrics"))
