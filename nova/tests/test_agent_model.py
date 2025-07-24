# nova/tests/test_agent_model.py
"""
Regression tests for the Agent model.

We keep these tests focused on things that should *never* change without
raising attention:
    • basic creation
    • __str__ implementation
    • the is_tool flag behaviour
    • relationship with Tool objects
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models import Agent, LLMProvider, Tool


User = get_user_model()


class AgentModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("bob", password="pwd")

        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type="openai",
            model="gpt-3.5-turbo",
            api_key="dummy",
        )

        self.tool = Tool.objects.create(
            user=self.user,
            name="Sample tool",
            description="Just a test",
            tool_type=Tool.ToolType.API,
            endpoint="https://example.com",
            is_active=True,
        )

    # ------------------------------------------------------------------ #
    #  Basic behaviour                                                   #
    # ------------------------------------------------------------------ #
    def test_create_agent_minimal(self):
        agent = Agent.objects.create(
            user=self.user,
            name="My agent",
            llm_provider=self.provider,
            system_prompt="You are helpful",
        )
        self.assertEqual(str(agent), "My agent")
        self.assertFalse(agent.is_tool)

    def test_agent_marked_as_tool(self):
        agent = Agent.objects.create(
            user=self.user,
            name="As tool",
            llm_provider=self.provider,
            system_prompt="Prompt",
            is_tool=True,
        )
        self.assertTrue(agent.is_tool)

    # ------------------------------------------------------------------ #
    #  Tool relationship                                                 #
    # ------------------------------------------------------------------ #
    def test_assign_regular_tools(self):
        agent = Agent.objects.create(
            user=self.user,
            name="Data-gatherer",
            llm_provider=self.provider,
            system_prompt="Prompt",
        )
        agent.tools.add(self.tool)
        agent.refresh_from_db()
        self.assertIn(self.tool, agent.tools.all())

    # ------------------------------------------------------------------ #
    #  Agent exposed as a tool for others                                #
    # ------------------------------------------------------------------ #
    def test_agent_can_reference_other_agents_as_tools(self):
        # Agent #1 that will be used as a tool
        tool_agent = Agent.objects.create(
            user=self.user,
            name="Sub agent",
            llm_provider=self.provider,
            system_prompt="Prompt",
            is_tool=True,
        )

        # Main agent that consumes Agent #1
        main_agent = Agent.objects.create(
            user=self.user,
            name="Main agent",
            llm_provider=self.provider,
            system_prompt="Prompt",
        )
        # AgentForm sets a symmetrical M2M called agent_tools – we replicate it:
        main_agent.agent_tools.add(tool_agent)

        self.assertIn(tool_agent, main_agent.agent_tools.all())
