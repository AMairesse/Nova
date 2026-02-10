from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.tests.factories import create_agent, create_provider, create_tool, create_user
from user_settings.views.tasks import TaskDefinitionForm


class UserSettingsTasksViewsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="tasks-alice", email="tasks-alice@example.com")
        self.other = create_user(username="tasks-bob", email="tasks-bob@example.com")
        self.provider = create_provider(self.user, name="provider-main")
        self.agent = create_agent(self.user, self.provider, name="agent-main")
        self.client.login(username="tasks-alice", password="testpass123")

    def _create_agent_cron_task(self, name: str = "Cron agent", is_active: bool = True) -> TaskDefinition:
        task = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=self.agent,
            prompt="Do work",
            run_mode=TaskDefinition.RunMode.NEW_THREAD,
            cron_expression="*/5 * * * *",
            timezone="UTC",
            is_active=is_active,
        )
        task.full_clean()
        task.save()
        return task

    def _create_email_task(self, name: str = "Email task") -> TaskDefinition:
        email_tool = create_tool(
            self.user,
            name="Email Builtin",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        task = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
            agent=self.agent,
            prompt="Check inbox",
            run_mode=TaskDefinition.RunMode.CONTINUOUS_MESSAGE,
            email_tool=email_tool,
            poll_interval_minutes=5,
            timezone="UTC",
            is_active=True,
        )
        task.full_clean()
        task.save()
        return task

    def _create_maintenance_task(self, name: str = "Nightly maintenance") -> TaskDefinition:
        task = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.MAINTENANCE,
            trigger_type=TaskDefinition.TriggerType.CRON,
            maintenance_task="continuous_nightly_daysegment_summaries_for_user",
            cron_expression="0 2 * * *",
            timezone="UTC",
            run_mode=TaskDefinition.RunMode.EPHEMERAL,
            is_active=True,
        )
        task.full_clean()
        task.save()
        return task

    def test_task_create_sets_user_and_agent_kind(self):
        response = self.client.post(
            reverse("user_settings:task_create"),
            data={
                "name": "Report",
                "trigger_type": TaskDefinition.TriggerType.CRON,
                "agent": str(self.agent.id),
                "prompt": "Daily report",
                "run_mode": TaskDefinition.RunMode.NEW_THREAD,
                "cron_expression": "0 8 * * *",
                "timezone": "UTC",
                "email_tool": "",
                "poll_interval_minutes": "5",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:tasks"))

        created = TaskDefinition.objects.get(user=self.user, name="Report")
        self.assertEqual(created.task_kind, TaskDefinition.TaskKind.AGENT)
        self.assertEqual(created.trigger_type, TaskDefinition.TriggerType.CRON)
        self.assertEqual(created.agent_id, self.agent.id)

    def test_task_definition_form_rejects_out_of_queryset_agent_and_tool(self):
        other_provider = create_provider(self.other, name="provider-other")
        other_agent = create_agent(self.other, other_provider, name="agent-other")
        other_email_tool = create_tool(
            self.other,
            name="Other Email",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )

        form = TaskDefinitionForm(
            data={
                "name": "Invalid ownership",
                "trigger_type": TaskDefinition.TriggerType.EMAIL_POLL,
                "agent": str(other_agent.id),
                "prompt": "x",
                "run_mode": TaskDefinition.RunMode.NEW_THREAD,
                "cron_expression": "",
                "timezone": "UTC",
                "email_tool": str(other_email_tool.id),
                "poll_interval_minutes": "5",
            },
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("agent", form.errors)
        self.assertIn("email_tool", form.errors)

    @patch("user_settings.views.tasks.ensure_continuous_nightly_summary_task_definition", side_effect=RuntimeError("boom"))
    def test_tasks_list_tolerates_system_task_ensure_failure(self, mocked_ensure):
        response = self.client.get(reverse("user_settings:tasks"))
        self.assertEqual(response.status_code, 200)
        mocked_ensure.assert_called_once_with(self.user)

    def test_task_toggle_active_switches_agent_task(self):
        task = self._create_agent_cron_task(name="Toggle me", is_active=True)
        response = self.client.post(reverse("user_settings:task_toggle_active", args=[task.id]))
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertFalse(task.is_active)

    def test_task_toggle_active_rejects_maintenance(self):
        task = self._create_maintenance_task(name="Do not toggle")
        response = self.client.post(reverse("user_settings:task_toggle_active", args=[task.id]))
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertTrue(task.is_active)

    @patch("user_settings.views.tasks.run_task_definition_cron.delay")
    @patch("user_settings.views.tasks.poll_task_definition_email.delay")
    @patch("user_settings.views.tasks.run_task_definition_maintenance.delay")
    def test_task_run_now_dispatches_expected_runner(self, mocked_maintenance, mocked_poll, mocked_cron):
        cron_task = self._create_agent_cron_task(name="Run cron now")
        email_task = self._create_email_task(name="Run email now")
        maintenance_task = self._create_maintenance_task(name="Run maintenance now")

        response = self.client.post(reverse("user_settings:task_run_now", args=[cron_task.id]))
        self.assertEqual(response.status_code, 302)
        mocked_cron.assert_called_once_with(cron_task.id)

        response = self.client.post(reverse("user_settings:task_run_now", args=[email_task.id]))
        self.assertEqual(response.status_code, 302)
        mocked_poll.assert_called_once_with(email_task.id)

        response = self.client.post(reverse("user_settings:task_run_now", args=[maintenance_task.id]))
        self.assertEqual(response.status_code, 302)
        mocked_maintenance.assert_called_once_with(maintenance_task.id)

    def test_task_clear_error_resets_last_error(self):
        task = self._create_agent_cron_task(name="Clear error")
        task.last_error = "Previous failure"
        task.save(update_fields=["last_error", "updated_at"])

        response = self.client.post(reverse("user_settings:task_clear_error", args=[task.id]))
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertIsNone(task.last_error)

    def test_task_cron_preview_handles_missing_invalid_and_valid(self):
        missing = self.client.get(reverse("user_settings:task_cron_preview"))
        self.assertEqual(missing.status_code, 400)
        self.assertFalse(missing.json()["valid"])

        invalid = self.client.get(
            reverse("user_settings:task_cron_preview"),
            {"cron_expression": "0 8 * *"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.json()["valid"])

        valid = self.client.get(
            reverse("user_settings:task_cron_preview"),
            {"cron_expression": "0 8 * * *"},
        )
        self.assertEqual(valid.status_code, 200)
        payload = valid.json()
        self.assertTrue(payload["valid"])
        self.assertIn("08:00", payload["description"])
