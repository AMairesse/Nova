from django.test import TestCase

from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.tests.factories import create_agent, create_provider, create_tool, create_tool_credential, create_user
from nova.tasks.template_registry import (
    DIFFERENTIAL_WATCH_TEMPLATE_ID,
    DRAFT_REPLIES_EVENTS_TEMPLATE_ID,
    MAIL_AGENDA_BRIEF_TEMPLATE_ID,
    WEBDAV_DOCUMENTS_TEMPLATE_ID,
    build_template_prefill_payload,
)


class NewTaskTemplateTests(TestCase):
    def setUp(self):
        self.user = create_user(username="routine-template-user", email="routine-template@example.com")
        provider = create_provider(self.user, name="routine-provider")
        self.agent = create_agent(self.user, provider, name="routine-agent")

    def _attach(self, subtype):
        tool = create_tool(
            self.user,
            name=f"{subtype}-tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
            python_path=f"nova.plugins.{subtype}",
        )
        self.agent.tools.add(tool)
        if subtype in {"email", "caldav", "webdav"}:
            create_tool_credential(
                self.user,
                tool,
                username="routine@example.com",
                password="secret",
                config={"caldav_url": "https://calendar.example.test"} if subtype == "caldav" else {"server_url": "https://webdav.example.test"} if subtype == "webdav" else {},
            )

    def test_templates_require_explicit_configuration_and_use_new_thread(self):
        self._attach("browser")
        initial = build_template_prefill_payload(
            self.user,
            DIFFERENTIAL_WATCH_TEMPLATE_ID,
            agent_id=self.agent.id,
            configuration={"sources": "https://example.test/feed"},
        )
        self.assertEqual(initial["run_mode"], TaskDefinition.RunMode.NEW_THREAD)
        self.assertIn("{{ previous_result }}", initial["prompt"])
        self.assertIn("{{ previous_run_at }}", initial["prompt"])
        self.assertIn("{{ routine_id }}", initial["prompt"])
        self.assertIsNone(
            build_template_prefill_payload(
                self.user,
                DIFFERENTIAL_WATCH_TEMPLATE_ID,
                agent_id=self.agent.id,
                configuration={},
            )
        )

    def test_each_template_checks_agent_tools(self):
        for subtype in ("email", "caldav", "webdav"):
            self._attach(subtype)
        for template_id, key in (
            (MAIL_AGENDA_BRIEF_TEMPLATE_ID, "theme"),
            (DRAFT_REPLIES_EVENTS_TEMPLATE_ID, "mail_scope"),
            (WEBDAV_DOCUMENTS_TEMPLATE_ID, "folder"),
        ):
            initial = build_template_prefill_payload(
                self.user,
                template_id,
                agent_id=self.agent.id,
                configuration={key: "configured scope"},
            )
            self.assertEqual(initial["agent"], self.agent.id)
            self.assertEqual(initial["run_mode"], TaskDefinition.RunMode.NEW_THREAD)
