from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric


User = get_user_model()


class AdminTerminalFailuresViewTests(TestCase):
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
        self.url = reverse("user_settings:admin-terminal-failures")
        self.partial_url = f"{self.url}?partial=1"

        TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-06",
            runtime_engine="react_terminal_v1",
            head_command="ls",
            failure_kind="invalid_arguments",
            count=3,
            last_seen_at="2026-04-06T10:00:00Z",
            recent_examples=["ls -h"],
            last_error="Unsupported ls flag: -h",
        )
        TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-06",
            runtime_engine="react_terminal_v1",
            head_command="unknowncmd",
            failure_kind="unknown_command",
            count=5,
            last_seen_at="2026-04-06T11:00:00Z",
            recent_examples=["unknowncmd --token <redacted>"],
            last_error="Unknown command: unknowncmd",
        )
        TerminalCommandFailureMetric.objects.create(
            bucket_date="2026-04-05",
            runtime_engine="legacy_terminal",
            head_command="pwd",
            failure_kind="unsupported_syntax",
            count=2,
            last_seen_at="2026-04-05T09:30:00Z",
            recent_examples=["pwd && ls"],
            last_error="Shell chaining with && and || is not supported.",
        )

    def test_login_required(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_non_staff_user_cannot_access_admin_terminal_failures(self):
        self.client.login(username="normal-user", password="pass123")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_staff_user_can_view_admin_terminal_failures_full_page(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/admin_terminal_failures.html")
        self.assertContains(response, "Terminal failures")
        self.assertContains(response, "unknowncmd")
        self.assertEqual(response.context["summary"]["total_events"], 10)
        self.assertEqual(response.context["summary"]["top_command"]["head_command"], "unknowncmd")

    def test_staff_user_can_view_admin_terminal_failures_partial(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(self.partial_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/admin_terminal_failures.html")
        self.assertContains(response, "Global aggregated failures")

    def test_admin_terminal_failures_filters_queryset(self):
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

    def test_dashboard_shows_admin_tab_only_for_staff_users(self):
        self.client.login(username="normal-user", password="pass123")
        normal_response = self.client.get(reverse("user_settings:dashboard"))
        self.assertEqual(normal_response.status_code, 200)
        self.assertNotContains(normal_response, 'data-bs-target="#pane-admin"', html=False)
        self.assertNotContains(normal_response, "Admin metrics")

        self.client.login(username="staff-user", password="pass123")
        staff_response = self.client.get(reverse("user_settings:dashboard"))
        self.assertEqual(staff_response.status_code, 200)
        self.assertContains(staff_response, 'data-bs-target="#pane-admin"', html=False)
        self.assertContains(staff_response, "Loading admin dashboard")

    def test_staff_user_menu_exposes_admin_metrics_link(self):
        self.client.login(username="staff-user", password="pass123")

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_settings:dashboard") + "#pane-admin")
