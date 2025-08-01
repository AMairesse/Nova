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
    Actor,
)

User = get_user_model()


class AjaxViewsTests(TestCase):
    # ------------------------------------------------------------------ #
    #  Test data                                                         #
    # ------------------------------------------------------------------ #
    def setUp(self) -> None:
        # Create user (triggers signal to create UserProfile and UserParameters)
        self.user = User.objects.create_user("alice", password="pwd")
        self.client.force_login(self.user)  # Use force_login for better isolation

        # Minimal thread + one message
        self.thread = Thread.objects.create(user=self.user, subject="Test")
        Message.objects.create(
            thread=self.thread,
            actor=Actor.USER,
            text="Hello",
            user=self.user,
        )

        # Dummy MCP tool
        self.tool_mcp = Tool.objects.create(
            user=self.user,
            name="Dummy MCP",
            description="test",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://example.com/mcp/",
            is_active=True,
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=self.tool_mcp,
            auth_type="none",
            config={},
        )

        # Dummy Builtin tool (CalDav)
        self.tool_builtin = Tool.objects.create(
            user=self.user,
            name="Dummy CalDav",
            description="test",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",  # Correct path for dynamic loading
            is_active=True,
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=self.tool_builtin,
            auth_type="basic",
            username="testuser",
            password="testpass",
            config={"caldav_url": "https://example.com/caldav/"},
        )

    def tearDown(self) -> None:
        # Explicit cleanup to avoid DB locks in async contexts
        self.client.logout()
        super().tearDown()

    # ------------------------------------------------------------------ #
    #  message_list                                                      #
    # ------------------------------------------------------------------ #
    def test_message_list_requires_login(self):
        self.client.logout()  # Ensure not logged in
        url = reverse("message_list")
        resp = self.client.get(url, {"thread_id": self.thread.id})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_message_list_success(self):
        url = reverse("message_list")
        resp = self.client.get(url, {"thread_id": self.thread.id})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello")

    # ------------------------------------------------------------------ #
    #  test_tool_connection (MCP)                                        #
    # ------------------------------------------------------------------ #
    @patch("nova.mcp.client.MCPClient.list_tools", create=True)
    def test_tool_connection_mcp_ok(self, mock_list_tools):
        """
        Simulate a successful “Test connection” POST on an MCP tool.
        """
        mock_list_tools.return_value = [
            {"name": "weather", "description": "Get weather"},
            {"name": "stocks",  "description": "Stock prices"},
        ]

        url = reverse("test_tool_connection", args=[self.tool_mcp.id])
        resp = self.client.post(url, {"auth_type": "none"})
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        self.assertEqual(data["status"], "success")
        self.assertIn("message", data)
        self.assertEqual(len(data["tools"]), 2)
        mock_list_tools.assert_called_once_with(user_id=self.user.id)

    #@patch("nova.mcp.client.MCPClient.list_tools", create=True)
    #def test_tool_connection_mcp_failure(self, mock_list_tools):
    #    """
    #    Simulate a failed MCP connection (e.g., exception).
    #    """
    #    mock_list_tools.side_effect = Exception("Connection failed")
    #
    #    url = reverse("test_tool_connection", args=[self.tool_mcp.id])
    #    resp = self.client.post(url, {"auth_type": "none"})
    #    self.assertEqual(resp.status_code, 200)
    #
    #    data = json.loads(resp.content)
    #    self.assertEqual(data["status"], "error")
    #    self.assertIn("Connection failed", data["message"])
    #    mock_list_tools.assert_called_once_with(user_id=self.user.id)

    def test_tool_connection_no_credential(self):
        """
        Test when credential is absent.
        """
        # Delete credential to simulate absence
        ToolCredential.objects.filter(tool=self.tool_mcp).delete()

        url = reverse("test_tool_connection", args=[self.tool_mcp.id])
        resp = self.client.post(url, {"auth_type": "none"})
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        self.assertEqual(data["status"], "error")
        self.assertIn("No credentials found", data["message"])

    @patch("nova.mcp.client.MCPClient.list_tools", create=True)
    def test_tool_connection_invalid_credential(self, mock_list_tools):
        """
        Test with invalid credential (e.g., wrong auth_type).
        """
        mock_list_tools.side_effect = ValueError("Invalid auth")

        url = reverse("test_tool_connection", args=[self.tool_mcp.id])
        resp = self.client.post(url, {"auth_type": "invalid"})
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content)
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid auth", data["message"])

    @patch("nova.mcp.client.MCPClient.list_tools", create=True)
    def test_tool_connection_rejects_get(self, mock_list_tools):
        url = reverse("test_tool_connection", args=[self.tool_mcp.id])

        resp = self.client.get(url)
        self.assertIn(resp.status_code, (403, 405))
        mock_list_tools.assert_not_called()

    # ------------------------------------------------------------------ #
    #  test_tool_connection (Builtin/CalDav)                             #
    # ------------------------------------------------------------------ #
    #@patch("nova.tools.builtins.caldav.test_caldav_access")
    #def test_tool_connection_builtin_ok(self, mock_test):
    #    """
    #    Simulate a successful test for builtin tool (e.g., CalDav).
    #    """
    #    mock_test.return_value = {"status": "success", "message": "Connected"}
    #
    #    url = reverse("test_tool_connection", args=[self.tool_builtin.id])
    #    resp = self.client.post(url, {
    #        "auth_type": "basic",
    #        "username": "testuser",
    #        "password": "testpass",
    #        "caldav_url": "https://example.com/caldav/"
    #    })
    #    self.assertEqual(resp.status_code, 200)
    #
    #    data = json.loads(resp.content)
    #    self.assertEqual(data["status"], "success")
    #    self.assertEqual(data["message"], "Connected")
    #    mock_test.assert_called_once_with(self.user, self.tool_builtin.id)

    #@patch("nova.tools.builtins.caldav.test_caldav_access")
    #def test_tool_connection_builtin_failure(self, mock_test):
    #    """
    #    Simulate a failed test for builtin tool (e.g., CalDav).
    #    """
    #    mock_test.return_value = {"status": "error", "message": "Connection failed"}
    #
    #    url = reverse("test_tool_connection", args=[self.tool_builtin.id])
    #    resp = self.client.post(url, {
    #        "auth_type": "basic",
    #        "username": "testuser",
    #        "password": "testpass",
    #        "caldav_url": "https://example.com/caldav/"
    #    })
    #    self.assertEqual(resp.status_code, 200)
    #
    #    data = json.loads(resp.content)
    #    self.assertEqual(data["status"], "error")
    #    self.assertIn("Connection failed", data["message"])
    #    mock_test.assert_called_once_with(self.user, self.tool_builtin.id)
