from django.test import AsyncClient, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch

from nova.models.models import (
    Tool,
    ToolCredential,
    Agent,
    LLMProvider,
    ProviderType,
)


# Fake MCPClient
class FakeMCPClient:
    def __init__(self, endpoint, credential=None, transport_type=None, user_id=None, thread_id=None):
        self.endpoint = endpoint

    async def alist_tools(self, force_refresh=False):
        return [
            {"name": "get_weather", "description": "desc", "input_schema": {}, "output_schema": {}},
            {"name": "get_alerts", "description": "desc2", "input_schema": {}, "output_schema": {}},
        ]

@patch("nova.mcp.client.MCPClient", FakeMCPClient)
class ToolsViewsTests(TestCase):
    async_client: AsyncClient

    @classmethod
    def setUpTestData(cls):
        cls.async_client = AsyncClient()
        
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pass"
        )
        self.client.login(username="alice", password="pass")

    def tearDown(self):
        self.client.logout()

    # ---------------------- create_tool ----------------------

    def test_create_tool_builtin_creates_tool_and_credential(self):
        url = reverse("create_tool")
        resp = self.client.post(
            url,
            data={
                "tool_type": Tool.ToolType.BUILTIN,
                "tool_subtype": "date",
                # name/description will be filled from metadata by the form
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=tools")

        tool = Tool.objects.get(user=self.user, tool_subtype="date")
        self.assertEqual(tool.tool_type, Tool.ToolType.BUILTIN)
        # A credential is created automatically for builtins (defaults to 'basic')
        cred = ToolCredential.objects.filter(user=self.user, tool=tool).first()
        self.assertIsNotNone(cred)
        self.assertEqual(cred.auth_type, "basic")

    def test_create_tool_api_invalid_redirects_with_error(self):
        # Missing required fields for API (name/description/endpoint)
        url = reverse("create_tool")
        resp = self.client.post(
            url,
            data={
                "tool_type": Tool.ToolType.API,
                # no name/description/endpoint
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("?tab=tools&error=1", resp["Location"])
        # form errors stored in session
        self.assertIn("tool_errors", self.client.session)

    # ---------------------- edit_tool ------------------------

    def _create_api_tool(self, **overrides) -> Tool:
        defaults = dict(
            user=self.user,
            name="API Tool",
            description="Desc",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
            is_active=True,
        )
        defaults.update(overrides)
        return Tool.objects.create(**defaults)

    def test_edit_tool_updates_fields_and_redirects(self):
        tool = self._create_api_tool()
        url = reverse("edit_tool", args=[tool.id])
        resp = self.client.post(
            url,
            data={
                "name": "Updated",
                "description": "Updated desc",
                "tool_type": Tool.ToolType.API,
                "endpoint": "https://api.example.com/v2",
                "transport_type": tool.transport_type,
                "input_schema": {},
                "output_schema": {},
                "is_active": True,
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=tools")

        tool.refresh_from_db()
        self.assertEqual(tool.name, "Updated")
        self.assertEqual(tool.description, "Updated desc")
        self.assertEqual(tool.endpoint, "https://api.example.com/v2")

    # ---------------------- delete_tool ----------------------

    def _create_provider(self, **overrides) -> LLMProvider:
        defaults = dict(
            user=self.user,
            name="Prov",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        defaults.update(overrides)
        return LLMProvider.objects.create(**defaults)

    def test_delete_tool_clears_relations_and_deletes(self):
        tool = Tool.objects.create(
            user=self.user,
            name="Builtin Browser",
            description="desc",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        ToolCredential.objects.create(user=self.user, tool=tool, auth_type="basic")

        provider = self._create_provider()
        agent = Agent.objects.create(
            user=self.user,
            name="Agent A",
            llm_provider=provider,
            system_prompt="x",
        )
        agent.tools.add(tool)

        # Pre-conditions
        self.assertTrue(agent.tools.filter(pk=tool.pk).exists())
        self.assertTrue(ToolCredential.objects.filter(tool=tool).exists())

        url = reverse("delete_tool", args=[tool.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=tools")

        # Relations cleared and tool/credential removed
        self.assertFalse(ToolCredential.objects.filter(tool=tool).exists())
        self.assertFalse(agent.tools.filter(pk=tool.pk).exists())
        self.assertFalse(Tool.objects.filter(pk=tool.pk).exists())

    # -------------------- configure_tool --------------------

    def test_configure_tool_caldav_creates_and_updates_credential(self):
        tool = Tool.objects.create(
            user=self.user,
            name="CalDav",
            description="desc",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )

        url = reverse("configure_tool", args=[tool.id])
        resp = self.client.post(
            url,
            data={
                "caldav_url": "https://cal.example.com",
                "username": "u1",
                "password": "p1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=tools")

        cred = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(
            cred.config,
            {"caldav_url": "https://cal.example.com", "username": "u1", "password": "p1"},
        )

        # Update with blank password should keep old one
        resp = self.client.post(
            url,
            data={
                "caldav_url": "https://cal2.example.com",
                "username": "u2",
                "password": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        cred.refresh_from_db()
        self.assertEqual(
            cred.config,
            {"caldav_url": "https://cal2.example.com", "username": "u2", "password": "p1"},
        )
