from django.test import TransactionTestCase
from django.urls import reverse

from nova.models.Tool import Tool
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)
from nova.webdav.service import build_webdav_mounts
from user_settings.views.tasks import TaskTemplateConfigurationForm


class TemplateConnectionSelectorTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(
            username="selector-user",
            email="selector-user@example.test",
        )
        self.other_user = create_user(
            username="other-selector-user",
            email="other-selector-user@example.test",
        )
        provider = create_provider(self.user, name="selector-provider")
        self.agent = create_agent(self.user, provider, name="selector-agent")
        self.client.force_login(self.user)

    def _tool(self, subtype, *, name, user=None, agent=None, config=None):
        user = user or self.user
        tool = create_tool(
            user,
            name=name,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
            python_path=f"nova.plugins.{subtype}",
        )
        if subtype == "email":
            default_config = {
                "imap_server": "imap.example.test",
                "username": "mail@example.test",
                "password": "mail-secret",
                "email": "mail@example.test",
            }
        elif subtype == "caldav":
            default_config = {
                "caldav_url": "https://calendar.example.test/dav",
                "username": "calendar@example.test",
                "password": "calendar-secret",
            }
        elif subtype == "webdav":
            default_config = {
                "server_url": "https://files.example.test/dav",
                "username": "files@example.test",
                "app_password": "webdav-secret",
            }
        else:
            default_config = {}
        create_tool_credential(user, tool, config={**default_config, **(config or {})})
        if agent is not None:
            agent.tools.add(tool)
        return tool

    def test_form_cleaned_connection_values_are_runtime_selector_strings(self):
        mail = self._tool("email", name="Mail")
        calendar = self._tool("caldav", name="Calendar")
        self.agent.tools.add(mail, calendar)

        form = TaskTemplateConfigurationForm(
            data={
                "agent": str(self.agent.pk),
                "value": "focus",
                "schedule": "08:00",
                "email_tool": mail.pk,
                "calendar_tool": calendar.pk,
            },
            user=self.user,
            template_id="mail_agenda_brief",
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["mailbox"], "mail@example.test")
        self.assertEqual(form.cleaned_data["calendar_account"], "calendar@example.test")
        self.assertIsInstance(form.cleaned_data.get("connection_instructions", []), list)

    def test_task_template_apply_puts_selected_runtime_selectors_in_prompt(self):
        mail = self._tool("email", name="Mail")
        calendar = self._tool("caldav", name="Calendar")
        self.agent.tools.add(mail, calendar)

        response = self.client.post(
            reverse("user_settings:task_template_apply", args=["mail_agenda_brief"]),
            data={
                "agent": self.agent.pk,
                "value": "work priorities",
                "schedule": "08:15",
                "email_tool": mail.pk,
                "calendar_tool": calendar.pk,
            },
        )

        self.assertRedirects(response, reverse("user_settings:task_create"), fetch_redirect_response=False)
        initial = self.client.session["task_template_initial"]
        prompt = initial["prompt"]
        self.assertIn("mail@example.test", prompt)
        self.assertIn("calendar@example.test", prompt)
        self.assertNotIn(str(mail), prompt)
        self.assertNotIn(str(calendar), prompt)

    def test_identical_tool_names_are_accepted_when_runtime_selectors_differ(self):
        first = self._tool(
            "email",
            name="Shared name",
            config={"username": "first@example.test", "email": "first@example.test"},
        )
        second = self._tool(
            "email",
            name="Shared name",
            config={"username": "second@example.test", "email": "second@example.test"},
        )
        calendar = self._tool("caldav", name="Calendar")
        self.agent.tools.add(first, second, calendar)

        form = TaskTemplateConfigurationForm(
            data={
                "agent": self.agent.pk,
                "value": "scope",
                "schedule": "08:00",
                "email_tool": second.pk,
                "calendar_tool": calendar.pk,
            },
            user=self.user,
            template_id="draft_replies_personal_events",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["mailbox"], "second@example.test")

    def test_duplicate_runtime_selectors_on_executing_agent_are_rejected(self):
        first = self._tool(
            "email",
            name="First",
            config={"username": "same@example.test", "email": "same@example.test"},
        )
        second = self._tool(
            "email",
            name="Second",
            config={"username": "same@example.test", "email": "same@example.test"},
        )
        calendar = self._tool("caldav", name="Calendar")
        self.agent.tools.add(first, second, calendar)

        form = TaskTemplateConfigurationForm(
            data={
                "agent": self.agent.pk,
                "value": "scope",
                "schedule": "08:00",
                "email_tool": first.pk,
                "calendar_tool": calendar.pk,
            },
            user=self.user,
            template_id="draft_replies_personal_events",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email_tool", form.errors)
        self.assertIn("ambiguous", str(form.errors["email_tool"]).lower())

    def test_webdav_duplicate_names_use_sorted_id_mounts_for_executing_agent(self):
        first = self._tool("webdav", name="Shared files")
        second = self._tool("webdav", name="Shared files")
        self.agent.tools.add(first, second)
        mounts = build_webdav_mounts(list(self.agent.tools.order_by("id")))
        expected_mounts = [mount.name for mount in mounts]

        form = TaskTemplateConfigurationForm(
            data={
                "agent": self.agent.pk,
                "value": "/incoming",
                "schedule": "08:00",
                "webdav_tool": second.pk,
            },
            user=self.user,
            template_id="webdav_documents_changes",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["webdav_source"], f"/webdav/{expected_mounts[1]}")

    def test_delegated_connection_instructions_include_child_id_and_mount(self):
        child = create_agent(
            self.user,
            self.agent.llm_provider,
            name="files-child",
            is_tool=True,
            tool_description="File helper",
        )
        webdav = self._tool("webdav", name="Child files", agent=child)
        self.agent.agent_tools.add(child)
        mount_name = build_webdav_mounts([webdav])[0].name

        response = self.client.post(
            reverse("user_settings:task_template_apply", args=["webdav_documents_changes"]),
            data={
                "agent": self.agent.pk,
                "value": "/incoming",
                "schedule": "08:00",
                "webdav_tool": webdav.pk,
            },
        )
        self.assertRedirects(response, reverse("user_settings:task_create"), fetch_redirect_response=False)
        prompt = self.client.session["task_template_initial"]["prompt"]
        self.assertIn(f"{child.pk}:{child.name}", prompt)
        self.assertIn(mount_name, prompt)

    def test_connection_from_another_user_is_rejected(self):
        foreign = self._tool("email", name="Foreign", user=self.other_user)
        self.agent.tools.add(foreign)

        form = TaskTemplateConfigurationForm(
            data={
                "agent": self.agent.pk,
                "value": "scope",
                "schedule": "08:00",
                "email_tool": foreign.pk,
            },
            user=self.user,
            template_id="draft_replies_personal_events",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email_tool", form.errors)
        self.assertIn("available", str(form.errors["email_tool"]).lower())
