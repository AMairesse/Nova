# nova/tests/test_ajax.py
"""
Smoke-tests for the AJAX / XHR endpoints:

• GET  /message-list/                 (message_list)
• POST /tool/test-connection/<id>/    (test_tool_connection)

The tests stay strictly at the HTTP level; any external calls
(MCP, CalDav, …) are stubbed out.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models import (
    Thread,
    Message,
    Tool,
    ToolCredential,
    UserProfile,
    Actor,
)

User = get_user_model()


class AjaxViewsTests(TestCase):
    # ------------------------------------------------------------------ #
    #  Test data                                                         #
    # ------------------------------------------------------------------ #
    def setUp(self) -> None:
        self.user = User.objects.create_user("alice", password="pwd")
        # The UI code expects a related profile object
        UserProfile.objects.create(user=self.user)          # ← fix ①

        # Minimal thread + one message
        self.thread = Thread.objects.create(user=self.user, subject="Test")
        Message.objects.create(
            thread=self.thread,
            actor=Actor.USER,
            text="Hello",
            user=self.user,
        )

        # Dummy MCP tool
        self.tool = Tool.objects.create(
            user=self.user,
            name="Dummy MCP",
            description="test",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://example.com/mcp/",
            is_active=True,
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="none",
            config={},
        )

    # ------------------------------------------------------------------ #
    #  message_list                                                      #
    # ------------------------------------------------------------------ #
    def test_message_list_requires_login(self):
        url = reverse("message_list")
        resp = self.client.get(url, {"thread_id": self.thread.id})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_message_list_success(self):
        self.client.login(username="alice", password="pwd")

        url = reverse("message_list")
        resp = self.client.get(url, {"thread_id": self.thread.id})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello")

    # ------------------------------------------------------------------ #
    #  test_tool_connection (MCP)                                        #
    # ------------------------------------------------------------------ #
    @patch("nova.mcp.client.MCPClient.list_tools", create=True)          # ← fix ②
    def test_tool_connection_ok(self, mock_list_tools):
        """
        Simulate a successful “Test connection” POST on an MCP tool.
        """
        mock_list_tools.return_value = [
            {"name": "weather", "description": "Get weather"},
            {"name": "stocks",  "description": "Stock prices"},
        ]

        self.client.login(username="alice", password="pwd")
        url = reverse("test_tool_connection", args=[self.tool.id])

        resp = self.client.post(url, {"auth_type": "none"})
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["tools"]), 2)
        mock_list_tools.assert_called_once_with(user_id=self.user.id)

    @patch("nova.mcp.client.MCPClient.list_tools", create=True)
    def test_tool_connection_rejects_get(self, mock_list_tools):
        self.client.login(username="alice", password="pwd")
        url = reverse("test_tool_connection", args=[self.tool.id])

        resp = self.client.get(url)
        self.assertIn(resp.status_code, (403, 405))
        mock_list_tools.assert_not_called()
