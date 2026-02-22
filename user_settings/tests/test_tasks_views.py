from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.models.UserObjects import UserProfile
from nova.tasks.template_registry import (
    THEMATIC_WATCH_MEMORY_THEME_LANGUAGE,
    THEMATIC_WATCH_MEMORY_THEME_TOPICS,
    THEMATIC_WATCH_MEMORY_TYPE,
)
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)
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

    def test_task_definition_form_email_trigger_warns_when_agent_cannot_use_selected_email_tool(self):
        selected_email_tool = create_tool(
            self.user,
            name="Email Trigger Tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )

        form = TaskDefinitionForm(
            data={
                "name": "Email warning task",
                "trigger_type": TaskDefinition.TriggerType.EMAIL_POLL,
                "agent": str(self.agent.id),
                "prompt": "Use trigger variables only",
                "run_mode": TaskDefinition.RunMode.NEW_THREAD,
                "cron_expression": "",
                "timezone": "UTC",
                "email_tool": str(selected_email_tool.id),
                "poll_interval_minutes": "5",
            },
            user=self.user,
        )

        warning = form.get_email_tool_access_warning()
        self.assertIsNotNone(warning)
        self.assertEqual(warning["agent_id"], str(self.agent.id))
        self.assertEqual(warning["email_tool_id"], str(selected_email_tool.id))
        self.assertEqual(
            warning["email_tool_label"],
            f"{selected_email_tool.name} (#{selected_email_tool.id})",
        )

    def test_task_definition_form_email_trigger_no_warning_when_agent_can_use_selected_email_tool(self):
        selected_email_tool = create_tool(
            self.user,
            name="Email Trigger Tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        self.agent.tools.add(selected_email_tool)

        form = TaskDefinitionForm(
            data={
                "name": "Email no warning task",
                "trigger_type": TaskDefinition.TriggerType.EMAIL_POLL,
                "agent": str(self.agent.id),
                "prompt": "Agent can access mail",
                "run_mode": TaskDefinition.RunMode.NEW_THREAD,
                "cron_expression": "",
                "timezone": "UTC",
                "email_tool": str(selected_email_tool.id),
                "poll_interval_minutes": "5",
            },
            user=self.user,
        )

        self.assertIsNone(form.get_email_tool_access_warning())

    @patch(
        "user_settings.views.tasks.ensure_continuous_nightly_summary_task_definition",
        side_effect=RuntimeError("boom"),
    )
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

    def test_task_view_renders_maintenance_details_and_prompt_template(self):
        task = self._create_maintenance_task(name="Nightly summaries")

        response = self.client.get(reverse("user_settings:task_view", args=[task.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nightly summaries")
        self.assertContains(response, "continuous_nightly_daysegment_summaries_for_user")
        self.assertContains(response, "You are generating a day summary for a continuous discussion.")
        self.assertContains(response, "{{day_label}}")
        self.assertContains(response, "{{transcript}}")

    def test_task_view_rejects_other_user_task(self):
        other_provider = create_provider(self.other, name="provider-other-task-view")
        other_agent = create_agent(self.other, other_provider, name="agent-other-task-view")
        foreign_task = TaskDefinition.objects.create(
            user=self.other,
            name="Other user task",
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=other_agent,
            prompt="x",
            run_mode=TaskDefinition.RunMode.NEW_THREAD,
            cron_expression="0 9 * * *",
            timezone="UTC",
            is_active=True,
        )

        response = self.client.get(reverse("user_settings:task_view", args=[foreign_task.id]))
        self.assertEqual(response.status_code, 404)

    def test_tasks_list_shows_view_button_for_maintenance_task(self):
        task = self._create_maintenance_task(name="Inspect me")

        response = self.client.get(reverse("user_settings:tasks"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_settings:task_view", args=[task.id]))

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

    def test_tasks_list_shows_predefined_tasks_button(self):
        response = self.client.get(reverse("user_settings:tasks"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_settings:task_templates"))

    def test_task_templates_list_marks_spam_template_unavailable_when_no_prerequisites(self):
        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Spam filtering")
        self.assertContains(response, "Unavailable")

    def test_task_templates_list_marks_spam_template_available_when_agent_has_configured_mail_tool(self):
        email_tool = create_tool(
            self.user,
            name="Work Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            email_tool,
            config={"email": "alice@example.com", "imap_server": "imap.example.com"},
        )
        self.agent.tools.add(email_tool)

        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Spam filtering")
        self.assertContains(response, "Available")

    def test_task_template_apply_prefills_create_form_with_ephemeral_mode_and_mailbox_name(self):
        email_tool = create_tool(
            self.user,
            name="Work Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            email_tool,
            config={"email": "alice@example.com", "imap_server": "imap.example.com"},
        )
        self.agent.tools.add(email_tool)

        apply_response = self.client.get(
            reverse("user_settings:task_template_apply", args=["email_spam_filter_basic"])
        )
        self.assertEqual(apply_response.status_code, 302)
        self.assertEqual(apply_response["Location"], reverse("user_settings:task_create"))

        create_response = self.client.get(reverse("user_settings:task_create"))
        self.assertEqual(create_response.status_code, 200)

        form = create_response.context["form"]
        self.assertEqual(form.initial.get("run_mode"), TaskDefinition.RunMode.EPHEMERAL)
        self.assertEqual(form.initial.get("trigger_type"), TaskDefinition.TriggerType.EMAIL_POLL)
        self.assertEqual(form.initial.get("agent"), self.agent.id)
        self.assertEqual(form.initial.get("email_tool"), email_tool.id)
        self.assertEqual(form.initial.get("name"), "Spam filtering - alice@example.com")

    def test_task_template_apply_redirects_back_when_template_unavailable(self):
        response = self.client.get(
            reverse("user_settings:task_template_apply", args=["email_spam_filter_basic"])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:task_templates"))

    def test_task_templates_list_marks_thematic_watch_unavailable_without_browser_capability(self):
        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thematic watch - weekly")
        self.assertContains(
            response,
            "No selectable agent can both browse the web and access memory. "
            "Add browser and memory tools directly or via sub-agents.",
        )

    def test_task_templates_list_marks_thematic_watch_available_with_direct_browser_tool(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool, memory_tool)

        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thematic watch - weekly")
        self.assertNotContains(
            response,
            "No selectable agent can both browse the web and access memory. "
            "Add browser and memory tools directly or via sub-agents.",
        )

    def test_task_templates_list_marks_thematic_watch_available_with_subagent_browser_tool(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        sub_agent = create_agent(
            self.user,
            self.provider,
            name="internet-sub-agent",
            is_tool=True,
            tool_description="internet helper",
        )
        sub_agent.tools.add(browser_tool)
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(memory_tool)
        self.agent.agent_tools.add(sub_agent)

        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thematic watch - weekly")
        self.assertNotContains(
            response,
            "No selectable agent can both browse the web and access memory. "
            "Add browser and memory tools directly or via sub-agents.",
        )

    def test_task_template_apply_prefills_thematic_watch_weekly(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool, memory_tool)

        apply_response = self.client.get(
            reverse("user_settings:task_template_apply", args=["thematic_watch_weekly"])
        )
        self.assertEqual(apply_response.status_code, 302)
        self.assertEqual(apply_response["Location"], reverse("user_settings:task_create"))

        create_response = self.client.get(reverse("user_settings:task_create"))
        self.assertEqual(create_response.status_code, 200)

        form = create_response.context["form"]
        self.assertEqual(form.initial.get("trigger_type"), TaskDefinition.TriggerType.CRON)
        self.assertEqual(form.initial.get("run_mode"), TaskDefinition.RunMode.NEW_THREAD)
        self.assertEqual(form.initial.get("cron_expression"), "0 6 * * 1")
        self.assertEqual(form.initial.get("agent"), self.agent.id)

    def test_task_templates_list_displays_guided_setup_for_available_thematic_watch(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool, memory_tool)

        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("user_settings:task_template_setup", args=["thematic_watch_weekly"]),
        )

    def test_task_template_setup_redirects_to_chat_with_prefill_and_selected_agent(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool)
        self.agent.tools.add(memory_tool)
        UserProfile.objects.update_or_create(user=self.user, defaults={"default_agent": self.agent})

        response = self.client.get(
            reverse("user_settings:task_template_setup", args=["thematic_watch_weekly"])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("continuous_home"), response["Location"])
        self.assertNotIn("agent_id=", response["Location"])
        self.assertIn("prefill_message=", response["Location"])
        self.assertIn("THEMATIC_WATCH_SETUP", response["Location"])
        self.assertIn(THEMATIC_WATCH_MEMORY_THEME_TOPICS, response["Location"])
        self.assertIn(THEMATIC_WATCH_MEMORY_THEME_LANGUAGE, response["Location"])
        self.assertIn(THEMATIC_WATCH_MEMORY_TYPE, response["Location"])

    def test_task_templates_list_disables_guided_setup_when_default_agent_has_no_memory_tool(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool, memory_tool)

        default_without_memory = create_agent(self.user, self.provider, name="default-no-memory")
        default_without_memory.tools.add(browser_tool)
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"default_agent": default_without_memory},
        )

        response = self.client.get(reverse("user_settings:task_templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guided setup")
        self.assertContains(
            response,
            "Guided setup requires the default agent to have access to the memory tool.",
        )

    def test_task_template_setup_rejects_when_default_agent_has_no_memory_tool(self):
        browser_tool = create_tool(
            self.user,
            name="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        memory_tool = create_tool(
            self.user,
            name="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(browser_tool, memory_tool)

        default_without_memory = create_agent(self.user, self.provider, name="default-no-memory")
        default_without_memory.tools.add(browser_tool)
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"default_agent": default_without_memory},
        )

        response = self.client.get(
            reverse("user_settings:task_template_setup", args=["thematic_watch_weekly"])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:task_templates"))

    def test_task_template_setup_redirects_back_when_thematic_watch_unavailable(self):
        response = self.client.get(
            reverse("user_settings:task_template_setup", args=["thematic_watch_weekly"])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:task_templates"))
