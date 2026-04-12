from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from django import forms
from django.test import TestCase
from django.urls import reverse
from django.contrib.messages import get_messages

from nova.models.APIToolOperation import APIToolOperation
from nova.models.Tool import Tool, ToolCredential
from nova.mcp import oauth_service as mcp_oauth_service
from user_settings.forms import ToolCredentialForm
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
            python_path="nova.plugins.search",
        )
        create_tool(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
            python_path="nova.plugins.python",
        )
        response = self.client.get(reverse("user_settings:tools"), {"partial": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/tool_table.html")
        self.assertContains(response, "Built-in capabilities")
        self.assertContains(response, "Capabilities with backends")
        self.assertContains(response, "Add connection")
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
            python_path="nova.plugins.search",
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

    @patch("nova.plugins.builtins.get_available_tool_types", return_value={"searxng": {"name": "Search"}})
    @patch(
        "nova.plugins.builtins.get_tool_type",
        return_value={
            "name": "Search",
            "description": "Search backend",
            "python_path": "nova.plugins.search",
            "input_schema": {},
            "output_schema": {},
        },
    )
    def test_create_search_backend_redirects_to_settings(self, mock_get_tool_type, mock_get_available):
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={"connection_kind": "search", "name": "My Search", "is_active": True},
        )
        self.assertEqual(response.status_code, 302)
        tool = Tool.objects.get(user=self.user, tool_subtype="searxng")
        self.assertEqual(response["Location"], reverse("user_settings:tool-edit", args=[tool.pk]))
        self.assertEqual(tool.python_path, "nova.plugins.search")

    def test_create_builtin_email_tool_keeps_custom_alias_name(self):
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={
                "connection_kind": "mail",
                "name": "Work Mailbox",
                "is_active": True,
            },
        )

        self.assertEqual(response.status_code, 302)
        tool = Tool.objects.get(user=self.user, tool_subtype="email")
        self.assertEqual(tool.name, "Work Mailbox")

    def test_create_builtin_email_tool_rejects_duplicate_alias_for_user(self):
        create_tool(
            self.user,
            name="Shared Inbox",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.plugins.mail",
        )

        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={
                "connection_kind": "mail",
                "name": "shared inbox",
                "is_active": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(
            Tool.objects.filter(user=self.user, tool_type=Tool.ToolType.BUILTIN, tool_subtype="email").count(),
            1,
        )

    def test_create_api_tool_validates_required_fields(self):
        response = self.client.post(
            reverse("user_settings:tool-add"),
            data={
                "connection_kind": "api",
                "description": "Missing fields",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.context["form"].errors)
        self.assertIn("endpoint", response.context["form"].errors)

    def test_create_form_only_lists_user_creatable_connection_types(self):
        response = self.client.get(reverse("user_settings:tool-add"))

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        choices = {value: label for value, label in form.fields["connection_kind"].choices if value}
        self.assertIn("mail", choices)
        self.assertIn("calendar", choices)
        self.assertIn("webdav", choices)
        self.assertIn("search", choices)
        self.assertIn("python", choices)
        self.assertIn("mcp", choices)
        self.assertIn("api", choices)
        self.assertNotIn("datetime", choices)
        self.assertNotIn("memory", choices)
        self.assertNotIn("browser", choices)
        self.assertNotIn("webapp", choices)

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
                "connection_mode": "none",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:tool-edit", args=[tool.id]))
        tool.refresh_from_db()
        self.assertEqual(tool.name, "Updated")
        self.assertEqual(tool.endpoint, "https://api.example.com/v2")

    def test_configure_route_redirects_to_settings(self):
        tool = create_tool(
            self.user,
            name="CRM API",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )

        response = self.client.get(reverse("user_settings:tool-configure", args=[tool.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:tool-edit", args=[tool.id]))

    def test_tool_settings_page_lists_operations_and_creates_api_key_credential(self):
        tool = create_tool(
            self.user,
            name="CRM API",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        APIToolOperation.objects.create(
            tool=tool,
            name="Create contact",
            slug="create-contact",
            description="Create a CRM contact",
            http_method=APIToolOperation.HTTPMethod.POST,
            path_template="/contacts",
        )

        response = self.client.get(reverse("user_settings:tool-edit", args=[tool.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API operations")
        self.assertContains(response, "Create contact")

        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={
                "name": tool.name,
                "description": tool.description,
                "endpoint": tool.endpoint,
                "connection_mode": "api_key",
                "username": "",
                "password": "",
                "token": "secret-key",
                "client_id": "",
                "client_secret": "",
                "api_key_name": "X-Service-Key",
                "api_key_in": "query",
            },
        )

        self.assertEqual(response.status_code, 302)
        credential = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(credential.auth_type, "api_key")
        self.assertEqual(credential.token, "secret-key")
        self.assertEqual(credential.config["api_key_name"], "X-Service-Key")
        self.assertEqual(credential.config["api_key_in"], "query")

    def test_api_operation_crud_views_work_under_api_tool(self):
        tool = create_tool(
            self.user,
            name="Billing API",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )

        create_response = self.client.post(
            reverse("user_settings:api-operation-add", args=[tool.id]),
            data={
                "name": "Create invoice",
                "slug": "create-invoice",
                "description": "Create an invoice",
                "http_method": APIToolOperation.HTTPMethod.POST,
                "path_template": "/invoices/{invoice_id}",
                "query_parameters_csv": "mode, locale",
                "body_parameter": "payload",
                "input_schema": '{"type":"object","required":["invoice_id","payload"]}',
                "output_schema": '{"type":"object"}',
                "is_active": True,
            },
        )

        self.assertEqual(create_response.status_code, 302)
        operation = APIToolOperation.objects.get(tool=tool, slug="create-invoice")
        self.assertEqual(operation.query_parameters, ["mode", "locale"])
        self.assertEqual(operation.body_parameter, "payload")

        update_response = self.client.post(
            reverse("user_settings:api-operation-edit", args=[tool.id, operation.id]),
            data={
                "name": "Create invoice v2",
                "slug": "create-invoice",
                "description": "Updated invoice creation",
                "http_method": APIToolOperation.HTTPMethod.PUT,
                "path_template": "/invoices/{invoice_id}",
                "query_parameters_csv": "mode",
                "body_parameter": "",
                "input_schema": '{"type":"object"}',
                "output_schema": '{"type":"object","properties":{"ok":{"type":"boolean"}}}',
                "is_active": True,
            },
        )

        self.assertEqual(update_response.status_code, 302)
        operation.refresh_from_db()
        self.assertEqual(operation.name, "Create invoice v2")
        self.assertEqual(operation.http_method, APIToolOperation.HTTPMethod.PUT)
        self.assertEqual(operation.query_parameters, ["mode"])

        delete_response = self.client.post(
            reverse("user_settings:api-operation-delete", args=[tool.id, operation.id]),
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(APIToolOperation.objects.filter(id=operation.id).exists())

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
            python_path="nova.plugins.browser",
        )
        response = self.client.post(reverse("user_settings:tool-delete", args=[system_tool.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Tool.objects.filter(pk=system_tool.pk).exists())

    @patch("user_settings.views.tool._get_builtin_metadata_for_tool")
    def test_configure_builtin_tool_saves_config(self, mock_get_builtin_metadata):
        mock_get_builtin_metadata.return_value = {
            "config_fields": [
                {"name": "username", "label": "User", "type": "text", "required": True},
                {"name": "password", "label": "Password", "type": "password", "required": False},
            ]
        }
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={"name": tool.name, "username": "alice", "password": "secret"},
        )
        self.assertEqual(response.status_code, 302)
        credential = tool.credentials.get(user=self.user)
        self.assertEqual(credential.config["username"], "alice")
        self.assertEqual(credential.config["password"], "secret")

    def test_configure_email_tool_displays_imap_and_smtp_sections(self):
        tool = create_tool(
            self.user,
            name="Mailbox",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.plugins.mail",
        )

        response = self.client.get(reverse("user_settings:tool-edit", args=[tool.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IMAP")
        self.assertContains(response, "SMTP")
        self.assertContains(response, "Enable email sending")

    def test_configure_builtin_uses_registry_even_when_python_path_is_stale(self):
        tool = create_tool(
            self.user,
            name="Mailbox",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="legacy.invalid.path",
        )

        response = self.client.get(reverse("user_settings:tool-edit", args=[tool.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IMAP")

    def test_configure_webdav_tool_shows_secret_placeholder_for_existing_app_password(self):
        tool = create_tool(
            self.user,
            name="WebDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="webdav",
            python_path="nova.plugins.webdav",
        )
        create_tool_credential(
            self.user,
            tool,
            config={
                "server_url": "https://cloud.example.com",
                "username": "alice",
                "app_password": "secret",
            },
        )

        response = self.client.get(reverse("user_settings:tool-edit", args=[tool.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="app_password"', html=False)
        self.assertContains(response, "Secret exists, leave blank to keep")

    def test_configure_non_builtin_creates_credential_if_missing(self):
        tool = create_tool(self.user, tool_type=Tool.ToolType.API, endpoint="https://api.example.com")
        response = self.client.post(
            reverse("user_settings:tool-edit", args=[tool.id]),
            data={
                "name": tool.name,
                "description": tool.description,
                "endpoint": tool.endpoint,
                "connection_mode": "basic",
                "username": "foo",
                "password": "bar",
                "token": "",
                "client_id": "",
                "client_secret": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        credential = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(credential.username, "foo")
        self.assertEqual(credential.password, "bar")

    @patch("nova.plugins.calendar.test_calendar_access", new_callable=AsyncMock)
    def test_tool_test_connection_builtin(self, mock_test_access):
        mock_test_access.return_value = {"status": "success"}
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_test_access.assert_awaited()

    @patch("nova.plugins.search.service.test_searxng_access", new_callable=AsyncMock)
    def test_tool_test_connection_search_backend(self, mock_test_access):
        mock_test_access.return_value = {"status": "success", "message": "OK"}
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.plugins.search",
        )
        create_tool_credential(
            self.user,
            tool,
            config={"searxng_url": "https://search.example.com", "num_results": 5},
        )

        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_test_access.assert_awaited_once_with(tool=tool)

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
            data={"connection_mode": "basic"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_instance.alist_tools.assert_awaited()

    def test_tool_credential_form_exposes_connection_modes_per_tool_type(self):
        mcp_tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        api_tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )

        mcp_form = ToolCredentialForm(user=self.user, tool=mcp_tool)
        api_form = ToolCredentialForm(user=self.user, tool=api_tool)

        mcp_choices = {value for value, _label in mcp_form.fields["connection_mode"].choices}
        api_choices = {value for value, _label in api_form.fields["connection_mode"].choices}
        self.assertIn("oauth_managed", mcp_choices)
        self.assertIn("token", mcp_choices)
        self.assertNotIn("oauth_managed", api_choices)
        self.assertNotIn("custom", mcp_choices)
        self.assertNotIn("custom", api_choices)
        self.assertNotIn("token_type", mcp_form.fields)
        self.assertNotIn("token_type", api_form.fields)

    @patch("user_settings.views.tool.mcp_oauth_service.start_mcp_oauth_flow", new_callable=AsyncMock)
    def test_tool_test_connection_mcp_oauth_redirects_when_authorization_needed(
        self,
        mock_start_flow,
    ):
        mock_start_flow.return_value = SimpleNamespace(
            authorization_url="https://auth.example.com/authorize?state=abc",
            state="abc",
        )
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )

        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={
                "connection_mode": "oauth_managed",
                "client_id": "preset-client",
                "connection_action": "connect_oauth",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "oauth_redirect")
        self.assertIn("authorization_url", payload)
        credential = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(credential.auth_type, "oauth_managed")
        self.assertEqual(credential.client_id, "preset-client")

    @patch("user_settings.views.tool.mcp_oauth_service.get_valid_mcp_access_token", new_callable=AsyncMock)
    def test_tool_test_connection_for_managed_oauth_tells_user_to_use_dedicated_button(
        self,
        mock_get_token,
    ):
        mock_get_token.side_effect = mcp_oauth_service.MCPReconnectRequired("Reconnect")
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )
        create_tool_credential(
            self.user,
            tool,
            auth_type="oauth_managed",
            config={"mcp_oauth": {"status": "reconnect_required"}},
        )

        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={"connection_mode": "oauth_managed", "connection_action": "verify"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("Reconnect required", response.json()["message"])

    @patch("user_settings.views.tool.MCPClient")
    def test_tool_test_connection_saves_posted_credential_on_first_create(self, mock_client):
        mock_instance = AsyncMock()
        mock_instance.alist_tools.return_value = []
        mock_client.return_value = mock_instance
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )

        response = self.client.post(
            reverse("user_settings:tool-test", args=[tool.id]),
            data={"connection_mode": "token", "token": "token-123"},
        )

        self.assertEqual(response.status_code, 200)
        credential = ToolCredential.objects.get(user=self.user, tool=tool)
        self.assertEqual(credential.auth_type, "token")
        self.assertEqual(credential.token, "token-123")

    @patch("user_settings.views.tool.MCPClient", side_effect=RuntimeError("boom"))
    def test_tool_test_connection_handles_errors(self, mock_client):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type="http",
        )
        with self.assertLogs("user_settings.views.tool", level="ERROR") as logs:
            response = self.client.post(
                reverse("user_settings:tool-test", args=[tool.id]),
                data={"connection_mode": "basic"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("boom", response.json()["message"])
        self.assertTrue(any("boom" in line for line in logs.output))

    @patch("nova.plugins.python.service.test_judge0_access", new_callable=AsyncMock)
    def test_tool_test_connection_codegen(self, mock_test):
        mock_test.return_value = {"status": "success"}
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
            python_path="nova.plugins.python",
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

    def test_mcp_oauth_callback_redirects_to_settings_on_success(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="oauth_managed",
            config={"mcp_oauth": {"status": "connected"}},
        )
        with patch(
            "user_settings.views.tool.mcp_oauth_service.complete_mcp_oauth_flow",
            new=AsyncMock(return_value=(tool, credential)),
        ), patch("user_settings.views.tool.MCPClient") as mock_client:
            mock_client.return_value.alist_tools = AsyncMock(return_value=[])
            response = self.client.get(
                reverse("user_settings:mcp-oauth-callback"),
                data={"state": "abc", "code": "code-123"},
            )

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(
            response["Location"],
            reverse("user_settings:tool-edit", args=[tool.pk]),
            messages,
        )

    def test_mcp_oauth_callback_redirects_to_tools_on_error(self):
        with patch(
            "user_settings.views.tool.mcp_oauth_service.complete_mcp_oauth_flow",
            new=AsyncMock(side_effect=RuntimeError("bad callback")),
        ):
            response = self.client.get(
                reverse("user_settings:mcp-oauth-callback"),
                data={"state": "abc", "code": "code-123"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:tools"))

    def test_tool_settings_renders_managed_oauth_inline_controls(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        create_tool_credential(
            self.user,
            tool,
            auth_type="oauth_managed",
            config={"mcp_oauth": {"status": "connected"}},
        )

        response = self.client.get(reverse("user_settings:tool-edit", args=[tool.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reconnect with OAuth")
        self.assertContains(response, 'name="connection_mode"', html=False)
        self.assertNotContains(response, 'name="token_type"', html=False)
        self.assertContains(response, 'id="managedOAuthPanel"', html=False)
        self.assertContains(response, 'id="oauthAdvancedDetails"', html=False)
        self.assertNotContains(response, 'id="oauthAdvancedToggle"', html=False)

    def test_tool_credential_form_keeps_existing_managed_oauth_when_mode_stays_selected(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="oauth_managed",
            config={"mcp_oauth": {"status": "connected"}},
        )

        form = ToolCredentialForm(
            data={
                "connection_mode": "oauth_managed",
                "username": "",
                "password": "",
                "token": "",
                "client_id": "",
                "client_secret": "",
                "api_key_name": "",
                "api_key_in": "header",
            },
            instance=credential,
            user=self.user,
            tool=tool,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.auth_type, "oauth_managed")

    def test_tool_credential_form_switching_away_from_managed_oauth_clears_managed_state(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="oauth_managed",
            config={"mcp_oauth": {"status": "connected", "last_error": "boom"}},
        )
        credential.client_id = "existing-client"
        credential.client_secret = "existing-secret"
        credential.access_token = "access-token"
        credential.refresh_token = "refresh-token"
        credential.save()

        form = ToolCredentialForm(
            data={
                "connection_mode": "token",
                "username": "",
                "password": "",
                "token": "manual-token",
                "client_id": "",
                "client_secret": "",
                "api_key_name": "",
                "api_key_in": "header",
            },
            instance=credential,
            user=self.user,
            tool=tool,
        )

        self.assertTrue(form.is_valid(), form.errors)
        credential = form.save()
        credential.refresh_from_db()
        self.assertEqual(credential.auth_type, "token")
        self.assertEqual(credential.token, "manual-token")
        self.assertIsNone(credential.client_id)
        self.assertIsNone(credential.client_secret)
        self.assertIsNone(credential.access_token)
        self.assertIsNone(credential.refresh_token)
        self.assertEqual(credential.config["mcp_oauth"]["status"], "disabled")

    def test_tool_credential_form_maps_legacy_manual_oauth_to_token_mode(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="oauth",
            token="legacy-token",
        )

        form = ToolCredentialForm(instance=credential, user=self.user, tool=tool)

        self.assertEqual(form.initial["connection_mode"], "token")

    def test_tool_credential_form_maps_legacy_custom_auth_to_none_mode(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="custom",
        )

        form = ToolCredentialForm(instance=credential, user=self.user, tool=tool)

        self.assertEqual(form.initial["connection_mode"], "none")

    def test_tool_credential_form_masks_existing_token_value(self):
        tool = create_tool(
            self.user,
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        credential = create_tool_credential(
            self.user,
            tool,
            auth_type="token",
            token="super-secret-token",
        )

        form = ToolCredentialForm(instance=credential, user=self.user, tool=tool)

        self.assertIsInstance(form.fields["token"].widget, forms.PasswordInput)
        self.assertFalse(form.fields["token"].widget.render_value)
        self.assertNotIn("super-secret-token", form["token"].as_widget())
