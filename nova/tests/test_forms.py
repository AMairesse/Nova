# nova/tests/test_forms.py
"""
Regression-tests for Nova forms.

Covers:
• UserParametersForm – Boolean flag, Langfuse config.
• LLMProviderForm – api_key preservation, config validation.
• AgentForm – Validation (cycles, is_tool), tools/agent_tools.
• ToolForm – Built-in autofill, validation for API/MCP, JSON schemas.
• ToolCredentialForm – Auth types, config (e.g., CalDav).
"""

from __future__ import annotations

import json
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils.encoding import force_str

from nova.forms import (
    UserParametersForm,
    LLMProviderForm,
    AgentForm,
    ToolForm,
    ToolCredentialForm,
)
from nova.models import (
    UserParameters,
    LLMProvider,
    Agent,
    Tool,
    ToolCredential,
    ProviderType,
    Actor,  # For any related if needed
)

User = get_user_model()


class UserParametersFormTests(TestCase):
    def setUp(self) -> None:
        # Create user (triggers signal for UserParameters)
        self.user = User.objects.create_user("alice", password="pwd")
        self.params = UserParameters.objects.get(user=self.user)

    def test_toggle_allow_langfuse(self):
        """Boolean flag is saved correctly."""
        form = UserParametersForm(
            {"allow_langfuse": "on"}, instance=self.params
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertTrue(inst.allow_langfuse)

        # Un-tick
        form = UserParametersForm(
            {"allow_langfuse": ""}, instance=self.params
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertFalse(inst.allow_langfuse)

    def test_langfuse_config_validation(self):
        """Langfuse keys and host are optional but stored encrypted."""
        form = UserParametersForm(
            {
                "allow_langfuse": "on",
                "langfuse_public_key": "pk-test",
                "langfuse_secret_key": "sk-test",
                "langfuse_host": "https://langfuse.example.com",
            },
            instance=self.params
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertEqual(inst.langfuse_public_key, "pk-test")
        self.assertEqual(inst.langfuse_secret_key, "sk-test")
        self.assertEqual(inst.langfuse_host, "https://langfuse.example.com")


class LLMProviderFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("bob", password="pwd")

    def test_api_key_preservation(self):
        """Existing api_key is preserved if blank in form."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",  # Required field
            api_key="original_key",
        )
        form = LLMProviderForm(
            {
                "name": "Test",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-3.5-turbo",  # Added required field
            },
            instance=provider
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertEqual(inst.api_key, "original_key")  # Preserved

    def test_additional_config_validation(self):
        """JSON config is optional and validated."""
        form = LLMProviderForm(
            {
                "name": "Test",
                "provider_type": ProviderType.OLLAMA,
                "model": "llama2",  # Added required field
                "additional_config": json.dumps({"temperature": 0.7}),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Invalid JSON
        form = LLMProviderForm(
            {
                "name": "Test",
                "provider_type": ProviderType.OLLAMA,
                "model": "llama2",  # Added required field
                "additional_config": "invalid json",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("additional_config", form.errors)


class AgentFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("charlie", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user, name="Test Provider", provider_type=ProviderType.MISTRAL, model="mistral-tiny"
        )
        self.tool = Tool.objects.create(user=self.user, name="Test Tool", tool_type=Tool.ToolType.BUILTIN)

    def test_is_tool_requires_description(self):
        """tool_description required if is_tool=True."""
        form = AgentForm(
            {
                "name": "Test Agent",
                "llm_provider": self.provider.id,
                "system_prompt": "Test prompt",
                "is_tool": True,
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("tool_description", form.errors)

    def test_agent_tools_no_cycle(self):
        """Prevent cycles in agent_tools."""
        agent1 = Agent.objects.create(
            user=self.user, name="Agent1", llm_provider=self.provider, is_tool=True, tool_description="Desc1"
        )
        agent2 = Agent.objects.create(
            user=self.user, name="Agent2", llm_provider=self.provider, is_tool=True, tool_description="Desc2"
        )
        form = AgentForm(
            {
                "name": "Agent3",
                "llm_provider": self.provider.id,
                "system_prompt": "Test",
                "agent_tools": [agent1.id, agent2.id],
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

        # Simulate cycle (would raise in clean/save)
        agent1.agent_tools.add(agent2)
        agent2.agent_tools.add(agent1)
        with self.assertRaises(ValidationError):
            agent1.clean()

    def test_tools_selection(self):
        """Tools and agent_tools are filtered by user."""
        form = AgentForm(user=self.user)
        self.assertIn(self.tool, form.fields["tools"].queryset)
        self.assertEqual(form.fields["agent_tools"].queryset.count(), 0)  # None yet


class ToolFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("dave", password="pwd")

    #
    # Built-in (“caldav”) helpers
    # ------------------------------------------------------------------ #
    def _builtin_post_data(self, subtype: str = "caldav") -> dict[str, str]:
        return {
            "tool_type": Tool.ToolType.BUILTIN,
            "tool_subtype": subtype,
            # Name / description intentionally left blank – must be auto-filled
            "is_active": "on",
        }

    #
    # 1) Built-in tool auto-fill
    # ------------------------------------------------------------------ #
    def test_builtin_autofill(self):
        """For builtin tools, name / description / python_path are injected."""
        data = self._builtin_post_data()
        form = ToolForm(data)
        self.assertTrue(form.is_valid(), form.errors)

        inst: Tool = form.save(commit=False)  # do **not** hit the DB
        self.assertEqual(force_str(inst.name), "CalDav")
        self.assertIn("CalDav", force_str(inst.description))
        self.assertEqual(inst.python_path, "nova.tools.builtins.caldav")
        self.assertEqual(inst.tool_type, Tool.ToolType.BUILTIN)
        self.assertTrue(inst.is_active)

    def test_builtin_invalid_subtype(self):
        """Invalid tool_subtype raises error."""
        data = self._builtin_post_data(subtype="invalid")
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("tool_subtype", form.errors)

    def test_json_schema_validation(self):
        """Input/output schemas must be valid JSON."""
        data = self._builtin_post_data()
        data["input_schema"] = "invalid json"
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("input_schema", form.errors)

    #
    # 2) Missing required fields for API / MCP tools
    # ------------------------------------------------------------------ #
    def test_api_requires_name_description_endpoint(self):
        """API tools without required fields must raise validation errors."""
        data = {
            "tool_type": Tool.ToolType.API,
            "is_active": "on",
            # name / description / endpoint omitted on purpose
        }
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("description", form.errors)
        self.assertIn("endpoint", form.errors)

    def test_mcp_requires_endpoint(self):
        """MCP tools must include at least a name, description and endpoint."""
        data = {
            "tool_type": Tool.ToolType.MCP,
            "name": "My remote tools",
            "description": "Container of remote tools",
            "is_active": "on",
            # endpoint missing
        }
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("endpoint", form.errors)


class ToolCredentialFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("eve", password="pwd")
        self.tool = Tool.objects.create(
            user=self.user,
            name="CalDav Tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )

    def test_caldav_config_required(self):
        """For CalDav tools, caldav_url is required."""
        form = ToolCredentialForm({"auth_type": "basic"}, tool=self.tool)
        self.assertFalse(form.is_valid())
        self.assertIn("caldav_url", form.errors)

    def test_auth_type_hides_fields(self):
        """Irrelevant fields are hidden based on auth_type."""
        form = ToolCredentialForm(initial={"auth_type": "none"}, tool=self.tool)
        self.assertTrue(isinstance(form.fields["username"].widget, forms.HiddenInput))

    def test_save_config(self):
        """Config JSON is updated correctly."""
        form = ToolCredentialForm(
            {
                "auth_type": "basic",
                "username": "testuser",
                "password": "testpass",
                "caldav_url": "https://caldav.example.com",
            },
            tool=self.tool,
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save(commit=False)
        self.assertIn("caldav_url", inst.config)
        self.assertEqual(inst.config["caldav_url"], "https://caldav.example.com")
