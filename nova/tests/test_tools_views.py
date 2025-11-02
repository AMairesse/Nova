from __future__ import annotations

from unittest.mock import AsyncMock, patch
from django.test import TestCase
from django.urls import reverse

from nova.models.Tool import Tool, ToolCredential
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)


class ToolsViewsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="alice")
        self.other = create_user(username="bob")
        self.client.login(username="alice", password="testpass123")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("user_settings:tools"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    @patch("user_settings.views.tool.check_and_create_judge0_tool")
    @patch("user_settings.views.tool.check_and_create_searxng_tool")
    def test_list_partial_renders_fragment_and_bootstraps_system_tools(
        self,
        mock_searxng,
        mock_judge0,
    ):
        create_tool(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.tools.builtins.searxng",
        )
        create_tool(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
            python_path="nova.tools.builtins.code_execution",
        )
        response = self.client.get(reverse("user_settings:tools"), {"partial": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/tool_table.html")
        self.assertTrue(
            Tool.objects.filter(user=None, tool_subtype__in={"searxng", "code_execution"}).exists()
        )
        mock_searxng.assert_called_once()
        mock_judge0.assert_called_once()

    @patch("user_settings.views.tool.check_and_create_judge0_tool")
    @patch("user_settings.views.tool.check_and_create_searxng_tool")
    def test_list_includes_user_and_system_tools(self, mock_searxng, mock_judge0):
        user_tool = create_tool(self.user, name="User Tool")
        system_tool = create_tool(
            user=None,
            name="System Tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.tools.builtins.searxng",
        )
        response = self.client.get(reverse("user_settings:tools"))
        tools = response.context["tools"]
        self.assertIn(user_tool, tools)
        self.assertIn(system_tool, tools)
        mock_searxng.assert_called_once()
        mock_judge0.assert_called_once()

    def test_create_tool_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={
                "tool_type": Tool.ToolType.API,
                "name": "API Tool",
                "description": "Test",
                "endpoint": "https://api.example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    @patch("user_settings.forms.get_available_tool_types", return_value={"date": {"name": "Date Tool"}})
    @patch(
        "user_settings.forms.get_tool_type",
        return_value={
            "name": "Date Tool",
            "description": "Dates",
            "python_path": "nova.tools.builtins.date",
            "input_schema": {},
            "output_schema": {},
        },
    )
    def test_create_builtin_tool_redirects_to_configure(self, mock_get_tool_type, mock_get_available):
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={"tool_type": Tool.ToolType.BUILTIN, "tool_subtype": "date", "is_active": True},
        )
        self.assertEqual(response.status_code, 302)
        tool = Tool.objects.get(user=self.user, tool_subtype="date")
        self.assertEqual(response["Location"], reverse("user_settings:tool-configure", args=[tool.pk]))
        self.assertEqual(tool.python_path, "nova.tools.builtins.date")

    def test_create_api_tool_validates_required_fields(self):
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={
                "tool_type": Tool.ToolType.API,
                "description": "Missing fields",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.context["form"].errors)
        self.assertIn("endpoint", response.context["form"].errors)

    def test_edit_tool_requires_owner(self):
        tool = create_tool(self.user, name="Owned Tool")
        self.client.logout()
        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={"name": "Attempt", "tool_type": tool.tool_type, "is_active": True},
        )
        self.assertEqual(response.status_code, 302)

        self.client.login(username="bob", password="testpass123")
        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={"name": "Hacked", "tool_type": tool.tool_type, "is_active": True},
        )
        self.assertEqual(response.status_code, 404)
        tool.refresh_from_db()
        self.assertEqual(tool.name, "Owned Tool")

    def test_edit_api_tool_updates_fields(self):
        tool = create_tool(
            self.user,
            name="API Tool",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com/v1",
            description="desc",
        )
        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={
                "name": "Updated",
                "description": "Updated desc",
                "tool_type": Tool.ToolType.API,
                "endpoint": "https://api.example.com/v2",
                "is_active": True,
                "input_schema": {},
                "output_schema": {},
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:dashboard") + "#pane-tools")
        tool.refresh_from_db()
        self.assertEqual(tool.name, "Updated")
        self.assertEqual(tool.endpoint, "https://api.example.com/v2")

    def test_delete_tool_requires_owner(self):
        tool = create_tool(self.user)
        self.client.logout()
        response = self.client.post(reverse("user_settings:tool-delete", args=[tool.id]))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="bob", password="testpass123")
        response = self.client.post(reverse("user_settings:tool-delete", args=[tool.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Tool.objects.filter(pk=tool.pk).exists())

    def test_delete_tool_clears_credentials_and_agent_relations(self):
        tool = create_tool(self.user, tool_type=Tool.ToolType.BUILTIN, tool_subtype="browser")
        create_tool_credential(self.user, tool, auth_type="basic")
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider=provider)
        agent.tools.add(tool)

        response = self.client.post(reverse("user_settings:tool-delete", args=[tool.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tool.objects.filter(pk=tool.pk).exists())
        self.assertFalse(ToolCredential.objects.filter(tool=tool).exists())
        self.assertFalse(agent.tools.filter(pk=tool.pk).exists())

    def test_system_tool_is_read_only(self):
        system_tool = Tool.objects.create(
            user=None,
            name="System Browser",
            description="read only",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )
        response = self.client.post(reverse("user_settings:tool-delete", args=[system_tool.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Tool.objects.filter(pk=system_tool.pk).exists())

    @patch("user_settings.views.tool.get_metadata")
    def test_configure_builtin_tool_saves_config(self, mock_get_metadata):
        mock_get_metadata.return_value = {
            "config_fields": [
                {"name": "username", "label": "User", "type": "text", "required": True},
                {"name": "password", "label": "Password", "type": "password", "required": False},
            ]
        }
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        response = self.client.post(
            reverse("user_settings:tool-configure", args=[tool.id]),
            data={"username": "alice", "password": "secret"},
        )
        self.assertEqual(response.status_code, 302)
        credential = tool.credentials.get(user=self.user)
        self.assertEqual(credential.config["username"], "alice")
        self.assertEqual(credential.config["password"], "secret")

    def test_configure_non_builtin_creates_credential_if_missing(self):
        tool = create_tool(self.user, tool_type=Tool.ToolType.API, endpoint="https://api.example.com")
        response = self.client.post(
            reverse("user_settings:tool-configure", args=[tool.id]),
            data={"auth_type": "basic", "username": "foo", "password": "bar"},
        )
        self.assertEqual(response.status_code, 302)
        credential = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(credential.username, "foo")
        self.assertEqual(credential.password, "bar")

    @patch("nova.tools.builtins.caldav.test_caldav_access", new_callable=AsyncMock)
    def test_tool_test_connection_builtin(self, mock_test_access):
        mock_test_access.return_value = {"status": "success"}
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_test_access.assert_awaited()

    @patch("user_settings.views.tool.MCPClient")
    def test_tool_test_connection_mcp(self, mock_client):
        mock_instance = AsyncMock()
        mock_instance.alist_tools.return_value = [{"name": "tool-a"}]
        mock_client.return_value = mock_instance
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )
        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={"auth_type": "basic"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_instance.alist_tools.assert_awaited()

    @patch("user_settings.views.tool.MCPClient", side_effect=RuntimeError("boom"))
    def test_tool_test_connection_handles_errors(self, mock_client):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )
        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={"auth_type": "basic"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("boom", response.json()["message"])

    @patch("nova.tools.builtins.code_execution.test_judge0_access", new_callable=AsyncMock)
    def test_tool_test_connection_codegen(self, mock_test):
        mock_test.return_value = {"status": "success"}
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
            python_path="nova.tools.builtins.code_execution",
        )
        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_test.assert_awaited()

    def test_tool_test_connection_requires_login(self):
        self.client.logout()
        tool = create_tool(self.user)
        response = self.client.post(reverse("user_settings:tool-test", args=[tool.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
