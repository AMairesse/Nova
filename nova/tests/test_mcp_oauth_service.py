from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase

from nova.mcp import oauth_service
from nova.models.Tool import Tool
from nova.tests.factories import create_tool, create_tool_credential, create_user


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.response


class MCPOAuthServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_user(username="mcp-oauth-user")
        self.tool = create_tool(
            self.user,
            name="You MCP",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://api.you.com/mcp",
            transport_type=Tool.TransportType.STREAMABLE_HTTP,
        )
        self.credential = create_tool_credential(
            self.user,
            self.tool,
            auth_type="oauth_managed",
            config={},
        )

    def test_start_mcp_oauth_flow_registers_client_and_caches_state(self):
        oauth_metadata = SimpleNamespace(authorization_endpoint="https://auth.example.com/authorize")
        client_info = SimpleNamespace(
            client_id="client-123",
            client_secret=None,
            token_endpoint_auth_method="none",
            client_secret_expires_at=None,
        )
        with patch(
            "nova.mcp.oauth_service._discover_oauth_metadata",
            new=AsyncMock(
                return_value=(
                    {
                        "resource_metadata_url": "https://api.you.com/mcp/.well-known/oauth-protected-resource",
                        "auth_server_url": "https://auth.example.com",
                        "authorization_endpoint": "https://auth.example.com/authorize",
                        "token_endpoint": "https://auth.example.com/token",
                        "registration_endpoint": "https://auth.example.com/register",
                    },
                    oauth_metadata,
                    "search",
                )
            ),
        ), patch(
            "nova.mcp.oauth_service._ensure_client_registration",
            new=AsyncMock(return_value=(client_info, "dynamic")),
        ):
            flow = async_to_sync(oauth_service.start_mcp_oauth_flow)(
                tool=self.tool,
                credential=self.credential,
                user=self.user,
                redirect_uri="https://nova.example.com/settings/tools/mcp/callback/",
            )

        self.assertIn("https://auth.example.com/authorize?", flow.authorization_url)
        self.assertIn("client_id=client-123", flow.authorization_url)
        cached = cache.get(f"mcp_oauth_flow::{flow.state}")
        self.assertIsNotNone(cached)
        self.credential.refresh_from_db()
        oauth_config = self.credential.config["mcp_oauth"]
        self.assertEqual(oauth_config["scope"], "search")
        self.assertEqual(oauth_config["client_registration_mode"], "dynamic")

    def test_complete_mcp_oauth_flow_persists_tokens(self):
        cache.set(
            "mcp_oauth_flow::state-123",
            {
                "user_id": self.user.id,
                "tool_id": self.tool.id,
                "credential_id": self.credential.id,
                "code_verifier": "verifier",
                "redirect_uri": "https://nova.example.com/settings/tools/mcp/callback/",
            },
            timeout=600,
        )
        self.credential.client_id = "client-123"
        self.credential.config = {
            "mcp_oauth": {
                "token_endpoint": "https://auth.example.com/token",
                "token_endpoint_auth_method": "none",
                "scope": "search",
            }
        }
        self.credential.save()
        response = httpx.Response(200, request=httpx.Request("POST", "https://auth.example.com/token"), content=b"{}")
        fake_client = _FakeAsyncClient(response)
        token_response = SimpleNamespace(
            access_token="access-123",
            refresh_token="refresh-123",
            token_type="Bearer",
            expires_in=3600,
            scope="search",
        )
        with patch("nova.mcp.oauth_service.httpx.AsyncClient", return_value=fake_client), patch(
            "nova.mcp.oauth_service.handle_token_response_scopes",
            new=AsyncMock(return_value=token_response),
        ):
            tool, credential = async_to_sync(oauth_service.complete_mcp_oauth_flow)(
                user=self.user,
                state="state-123",
                code="auth-code",
            )

        self.assertEqual(tool.id, self.tool.id)
        credential.refresh_from_db()
        self.assertEqual(credential.access_token, "access-123")
        self.assertEqual(credential.refresh_token, "refresh-123")
        self.assertEqual(credential.config["mcp_oauth"]["status"], "connected")
        self.assertIsNone(cache.get("mcp_oauth_flow::state-123"))

    def test_get_valid_access_token_refreshes_when_needed(self):
        self.credential.access_token = None
        self.credential.refresh_token = "refresh-123"
        self.credential.config = {
            "mcp_oauth": {
                "token_endpoint": "https://auth.example.com/token",
                "token_endpoint_auth_method": "none",
                "scope": "search",
            }
        }
        self.credential.client_id = "client-123"
        self.credential.save()
        response = httpx.Response(200, request=httpx.Request("POST", "https://auth.example.com/token"), content=b"{}")
        fake_client = _FakeAsyncClient(response)
        token_response = SimpleNamespace(
            access_token="fresh-token",
            refresh_token="fresh-refresh",
            token_type="Bearer",
            expires_in=3600,
            scope="search",
        )
        with patch("nova.mcp.oauth_service.httpx.AsyncClient", return_value=fake_client), patch(
            "nova.mcp.oauth_service.handle_token_response_scopes",
            new=AsyncMock(return_value=token_response),
        ):
            token = async_to_sync(oauth_service.get_valid_mcp_access_token)(
                tool=self.tool,
                credential=self.credential,
                user=self.user,
            )

        self.assertEqual(token, "fresh-token")
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.config["mcp_oauth"]["status"], "connected")

    def test_get_valid_access_token_marks_reconnect_when_refresh_fails(self):
        self.credential.access_token = None
        self.credential.refresh_token = "refresh-123"
        self.credential.client_id = "client-123"
        self.credential.config = {
            "mcp_oauth": {
                "token_endpoint": "https://auth.example.com/token",
                "token_endpoint_auth_method": "none",
                "scope": "search",
            }
        }
        self.credential.save()
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "https://auth.example.com/token"),
            content=b'{"error":"invalid_grant"}',
        )
        fake_client = _FakeAsyncClient(response)
        with patch("nova.mcp.oauth_service.httpx.AsyncClient", return_value=fake_client):
            with self.assertRaises(oauth_service.MCPReconnectRequired):
                async_to_sync(oauth_service.get_valid_mcp_access_token)(
                    tool=self.tool,
                    credential=self.credential,
                    user=self.user,
                )

        self.credential.refresh_from_db()
        self.assertEqual(self.credential.config["mcp_oauth"]["status"], "reconnect_required")
        self.assertIsNone(self.credential.access_token)
        self.assertIsNone(self.credential.refresh_token)
