from django.test import TestCase

from nova.bootstrap import BootstrapSummary, ensure_email_agent
from nova.models.Tool import Tool
from nova.tests.factories import (
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)


class BootstrapEmailAgentTests(TestCase):
    def setUp(self):
        self.user = create_user(username="bootstrap-user", email="bootstrap-user@example.com")
        self.provider = create_provider(self.user, name="Bootstrap Provider")
        self.date_tool = create_tool(
            self.user,
            name="Date Tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="date",
            python_path="nova.tools.builtins.date",
        )

    def test_ensure_email_agent_attaches_all_configured_email_tools(self):
        work_tool = create_tool(
            self.user,
            name="Work Mailbox",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        personal_tool = create_tool(
            self.user,
            name="Personal Mailbox",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            work_tool,
            config={
                "imap_server": "imap.work.example.com",
                "username": "work@example.com",
                "password": "secret",
            },
        )
        create_tool_credential(
            self.user,
            personal_tool,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "personal@example.com",
                "password": "secret",
            },
        )

        summary = BootstrapSummary()
        agent = ensure_email_agent(
            user=self.user,
            provider=self.provider,
            tools={"date_time": self.date_tool},
            summary=summary,
        )

        self.assertIsNotNone(agent)
        email_tool_ids = set(
            agent.tools.filter(tool_subtype="email").values_list("id", flat=True)
        )
        self.assertSetEqual(email_tool_ids, {work_tool.id, personal_tool.id})
