# nova/tests/test_tool_model.py
from __future__ import annotations

import math

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from nova.models import Tool, Agent, LLMProvider, ProviderType

User = get_user_model()


class ToolModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("alice", password="pwd")

        # Minimal provider so we can create Agents later on
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",
            api_key="dummy",
        )

    # ------------------------------------------------------------------ #
    #  Creation helpers                                                  #
    # ------------------------------------------------------------------ #
    def test_create_builtin_tool(self):
        tool = Tool.objects.create(
            user=self.user,
            name="CalDav",
            description="Calendar helper",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="math.sqrt",          # any callable for the test
            is_active=True,
        )
        self.assertEqual(str(tool), "CalDav (builtin)")
        self.assertTrue(tool.is_active)

    def test_create_api_tool_requires_endpoint(self):
        tool = Tool(
            user=self.user,
            name="Weather API",
            description="Get weather",
            tool_type=Tool.ToolType.API,
            # endpoint intentionally missing
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()                 # should raise because endpoint is mandatory

    def test_create_mcp_tool(self):
        tool = Tool.objects.create(
            user=self.user,
            name="Remote MCP",
            description="Container of remote tools",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://example.com/mcp/",
            is_active=True,
        )
        self.assertEqual(tool.endpoint, "https://example.com/mcp/")

    # ------------------------------------------------------------------ #
    #  Filtering helpers                                                 #
    # ------------------------------------------------------------------ #
    def test_get_active_tools_only_returns_active(self):
        Tool.objects.create(
            user=self.user,
            name="Active",
            description="on",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="math.sqrt",
            is_active=True,
        )
        Tool.objects.create(
            user=self.user,
            name="Inactive",
            description="off",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="math.sqrt",
            is_active=False,
        )
        active_tools = Tool.objects.filter(is_active=True)
        self.assertEqual(active_tools.count(), 1)
        self.assertEqual(active_tools.first().name, "Active")

    # ------------------------------------------------------------------ #
    #  Agent ↔ Tool relationship                                         #
    # ------------------------------------------------------------------ #
    def test_agent_tool_association(self):
        tool = Tool.objects.create(
            user=self.user,
            name="Sample tool",
            description="desc",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="math.sqrt",
            is_active=True,
        )
        agent = Agent.objects.create(
            user=self.user,
            name="My agent",
            llm_provider=self.provider,
            system_prompt="You are helpful",
        )
        agent.tools.add(tool)
        self.assertIn(tool, agent.tools.all())
