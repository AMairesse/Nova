from django.test import TestCase

from nova.bootstrap import bootstrap_default_setup
from nova.models.AgentConfig import AgentConfig
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)


class BootstrapSkillsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="bootstrap-user", email="bootstrap-user@example.com")
        self.provider = create_provider(self.user, name="Bootstrap Provider")

        # Ensure internet/code dependencies are discoverable during bootstrap.
        create_tool(
            self.user,
            name="SearXNG",
            tool_subtype="searxng",
            python_path="nova.tools.builtins.searxng",
        )
        create_tool(
            self.user,
            name="Judge0",
            tool_subtype="code_execution",
            python_path="nova.tools.builtins.code_execution",
        )

    def _create_email_tool(self, name: str, username: str):
        tool = create_tool(
            self.user,
            name=name,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            tool,
            config={
                "imap_server": "imap.example.com",
                "username": username,
                "password": "secret",
                "enable_sending": False,
            },
        )
        return tool

    def _create_caldav_tool(self, name: str, username: str):
        tool = create_tool(
            self.user,
            name=name,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def test_bootstrap_attaches_mail_and_caldav_to_nova_and_detaches_legacy_subagents(self):
        work_mail = self._create_email_tool("Work Mail", "work@example.com")
        personal_mail = self._create_email_tool("Personal Mail", "personal@example.com")
        work_calendar = self._create_caldav_tool("Work Calendar", "work@example.com")
        personal_calendar = self._create_caldav_tool("Personal Calendar", "personal")

        legacy_calendar_agent = create_agent(
            self.user,
            self.provider,
            name="Calendar Agent",
            is_tool=True,
            tool_description="Legacy calendar tool-agent",
        )
        legacy_email_agent = create_agent(
            self.user,
            self.provider,
            name="Email Agent",
            is_tool=True,
            tool_description="Legacy email tool-agent",
        )
        internet_agent = create_agent(
            self.user,
            self.provider,
            name="Internet Agent",
            is_tool=True,
            tool_description="Internet specialist",
        )
        code_agent = create_agent(
            self.user,
            self.provider,
            name="Code Agent",
            is_tool=True,
            tool_description="Code specialist",
        )
        existing_nova = create_agent(
            self.user,
            self.provider,
            name="Nova",
            system_prompt="Existing Nova prompt",
            is_tool=False,
        )
        existing_nova.agent_tools.add(
            legacy_calendar_agent,
            legacy_email_agent,
            internet_agent,
            code_agent,
        )

        summary = bootstrap_default_setup(self.user)

        nova = AgentConfig.objects.get(user=self.user, name="Nova")
        email_tool_ids = set(nova.tools.filter(tool_subtype="email").values_list("id", flat=True))
        caldav_tool_ids = set(nova.tools.filter(tool_subtype="caldav").values_list("id", flat=True))

        self.assertSetEqual(email_tool_ids, {work_mail.id, personal_mail.id})
        self.assertSetEqual(caldav_tool_ids, {work_calendar.id, personal_calendar.id})
        self.assertTrue(nova.agent_tools.filter(name="Internet Agent").exists())
        self.assertTrue(nova.agent_tools.filter(name="Code Agent").exists())
        self.assertFalse(nova.agent_tools.filter(name="Calendar Agent").exists())
        self.assertFalse(nova.agent_tools.filter(name="Email Agent").exists())
        self.assertTrue(
            any(
                "Detached legacy sub-agents from Nova" in note
                for note in summary.get("notes", [])
            )
        )

    def test_bootstrap_does_not_create_calendar_or_email_tool_agents(self):
        self._create_email_tool("Work Mail", "work@example.com")
        self._create_caldav_tool("Work Calendar", "work@example.com")

        bootstrap_default_setup(self.user)

        self.assertFalse(
            AgentConfig.objects.filter(
                user=self.user,
                name__in=["Calendar Agent", "Email Agent"],
            ).exists()
        )
