from __future__ import annotations

from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from nova.mcp.service import (
    MCPServiceError,
    call_mcp_tool,
    describe_mcp_tool,
    list_mcp_tools,
)
from nova.models.Tool import Tool
from nova.tests.factories import create_tool, create_tool_credential, create_user


class MCPServiceTests(TestCase):
    def setUp(self):
        self.user = create_user(username="mcp-service-user")
        self.tool = create_tool(
            self.user,
            name="Notion MCP",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type=Tool.TransportType.STREAMABLE_HTTP,
        )
        create_tool_credential(
            self.user,
            self.tool,
            auth_type="token",
            token="mcp-token",
        )

    def test_list_and_describe_mcp_tools(self):
        client = AsyncMock()
        client.alist_tools = AsyncMock(
            return_value=[
                {
                    "name": "list_pages",
                    "description": "List pages",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                }
            ]
        )

        with patch("nova.mcp.service._build_client", new_callable=AsyncMock, return_value=client):
            listed = async_to_sync(list_mcp_tools)(tool=self.tool, user=self.user)
            described = async_to_sync(describe_mcp_tool)(
                tool=self.tool,
                user=self.user,
                tool_name="list_pages",
            )

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "list_pages")
        self.assertEqual(described["tool"]["name"], "list_pages")
        self.assertEqual(described["server"]["name"], "Notion MCP")

    def test_call_mcp_tool_validates_input_and_extracts_text_artifacts(self):
        client = AsyncMock()
        client.alist_tools = AsyncMock(
            return_value=[
                {
                    "name": "export_report",
                    "description": "Export report",
                    "input_schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                    },
                    "output_schema": {"type": "object"},
                }
            ]
        )
        client.acall = AsyncMock(
            return_value={
                "report": {
                    "type": "text",
                    "filename": "report",
                    "text": "ready",
                }
            }
        )

        with patch("nova.mcp.service._build_client", new_callable=AsyncMock, return_value=client):
            result = async_to_sync(call_mcp_tool)(
                tool=self.tool,
                user=self.user,
                tool_name="export_report",
                payload={"query": "roadmap"},
            )

        self.assertEqual(result["payload"]["tool"]["name"], "export_report")
        self.assertEqual(result["payload"]["result"]["report"]["text"], "ready")
        self.assertEqual(len(result["extractable_artifacts"]), 1)
        self.assertEqual(result["extractable_artifacts"][0].path, "report.txt")
        self.assertEqual(result["extractable_artifacts"][0].content, b"ready")
        client.acall.assert_awaited_once_with("export_report", query="roadmap")

    def test_call_mcp_tool_rejects_invalid_input_schema(self):
        client = AsyncMock()
        client.alist_tools = AsyncMock(
            return_value=[
                {
                    "name": "export_report",
                    "description": "Export report",
                    "input_schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                    },
                    "output_schema": {"type": "object"},
                }
            ]
        )

        with patch("nova.mcp.service._build_client", new_callable=AsyncMock, return_value=client):
            with self.assertRaises(MCPServiceError) as cm:
                async_to_sync(call_mcp_tool)(
                    tool=self.tool,
                    user=self.user,
                    tool_name="export_report",
                    payload={},
                )

        self.assertIn("Input validation failed", str(cm.exception))

