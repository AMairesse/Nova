from __future__ import annotations

import httpx
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from nova.api_tools.service import (
    APIServiceError,
    call_api_operation,
    describe_api_operation,
    list_api_operations,
)
from nova.models.APIToolOperation import APIToolOperation
from nova.models.Tool import Tool, ToolCredential
from nova.tests.factories import (
    create_tool,
    create_tool_credential,
    create_user,
)


class APIToolServiceTests(TestCase):
    def setUp(self):
        self.user = create_user(username="api-service-user")
        self.tool = create_tool(
            self.user,
            name="Billing API",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        create_tool_credential(
            self.user,
            self.tool,
            auth_type="api_key",
            token="secret-api-key",
            config={"api_key_name": "X-API-Key", "api_key_in": "header"},
        )
        self.operation = APIToolOperation.objects.create(
            tool=self.tool,
            name="Create invoice",
            slug="create_invoice",
            description="Create invoice",
            http_method=APIToolOperation.HTTPMethod.POST,
            path_template="/invoices/{invoice_id}",
            query_parameters=["mode"],
            body_parameter="payload",
            input_schema={
                "type": "object",
                "required": ["invoice_id", "payload"],
                "properties": {
                    "invoice_id": {"type": "integer"},
                    "mode": {"type": "string"},
                    "payload": {"type": "object"},
                },
            },
            output_schema={"type": "object"},
        )

    def test_list_and_describe_api_operations(self):
        listed = async_to_sync(list_api_operations)(tool=self.tool)
        described = async_to_sync(describe_api_operation)(
            tool=self.tool,
            operation_selector="create_invoice",
        )

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["slug"], "create_invoice")
        self.assertEqual(described["service"]["name"], "Billing API")
        self.assertEqual(described["operation"]["path_template"], "/invoices/{invoice_id}")

    def test_call_api_operation_maps_path_query_body_and_header_api_key(self):
        request = httpx.Request(
            "POST",
            "https://api.example.com/invoices/42?mode=draft",
        )
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True, "invoice_id": 42},
            request=request,
        )
        with patch("nova.api_tools.service.safe_http_request", new=AsyncMock(return_value=response)) as mocked_request:
            result = async_to_sync(call_api_operation)(
                tool=self.tool,
                user=self.user,
                operation_selector="create_invoice",
                payload={
                    "invoice_id": 42,
                    "mode": "draft",
                    "payload": {"amount": 199},
                },
            )

        self.assertEqual(result["body_kind"], "json")
        self.assertTrue(result["payload"]["response"]["json"]["ok"])
        self.assertEqual(
            mocked_request.await_args.args[:2],
            ("POST", "https://api.example.com/invoices/42"),
        )
        self.assertEqual(
            mocked_request.await_args.kwargs["params"],
            {"mode": "draft"},
        )
        self.assertEqual(
            mocked_request.await_args.kwargs["json"],
            {"amount": 199},
        )
        self.assertEqual(
            mocked_request.await_args.kwargs["headers"]["X-API-Key"],
            "secret-api-key",
        )

    def test_call_api_operation_requires_declared_fields(self):
        with self.assertRaises(APIServiceError) as cm:
            async_to_sync(call_api_operation)(
                tool=self.tool,
                user=self.user,
                operation_selector="create_invoice",
                payload={
                    "invoice_id": 42,
                    "payload": {"amount": 199},
                    "unexpected": "value",
                },
            )

        self.assertIn("Unknown input fields", str(cm.exception))

    def test_call_api_operation_blocks_private_endpoint_before_network(self):
        self.tool.endpoint = "http://127.0.0.1:8000"
        self.tool.save(update_fields=["endpoint"])

        with self.assertRaises(APIServiceError) as cm:
            async_to_sync(call_api_operation)(
                tool=self.tool,
                user=self.user,
                operation_selector="create_invoice",
                payload={
                    "invoice_id": 42,
                    "mode": "draft",
                    "payload": {"amount": 199},
                },
            )

        self.assertIn("blocked by network policy", str(cm.exception))

    def test_call_api_operation_redacts_sensitive_request_and_response_fields(self):
        ToolCredential.objects.filter(user=self.user, tool=self.tool).update(
            auth_type="api_key",
            token="secret-api-key",
            config={"api_key_name": "apiKey", "api_key_in": "query"},
        )
        request = httpx.Request(
            "POST",
            "https://api.example.com/invoices/42?mode=draft&apiKey=secret-api-key",
        )
        response = httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "session=topsecret",
                "server": "gunicorn",
                "location": "https://api.example.com/next?apiKey=secret-api-key",
            },
            json={"ok": True, "echo": "secret-api-key"},
            request=request,
        )
        with patch("nova.api_tools.service.safe_http_request", new=AsyncMock(return_value=response)):
            result = async_to_sync(call_api_operation)(
                tool=self.tool,
                user=self.user,
                operation_selector="create_invoice",
                payload={
                    "invoice_id": 42,
                    "mode": "draft",
                    "payload": {"amount": 199},
                },
            )

        envelope = result["payload"]
        self.assertEqual(envelope["request"]["query"]["apiKey"], "[redacted]")
        self.assertIn("apiKey=%5Bredacted%5D", envelope["request"]["url"])
        self.assertEqual(envelope["response"]["json"]["echo"], "[redacted]")
        self.assertEqual(
            envelope["response"]["headers"]["location"],
            "https://api.example.com/next?apiKey=%5Bredacted%5D",
        )
        self.assertNotIn("server", envelope["response"]["headers"])
        self.assertNotIn("set-cookie", {key.lower() for key in envelope["response"]["headers"].keys()})

    def test_call_api_operation_keeps_configured_host_when_path_value_looks_like_a_url(self):
        operation = APIToolOperation.objects.create(
            tool=self.tool,
            name="Fetch invoice",
            slug="fetch_invoice",
            description="Fetch invoice",
            http_method=APIToolOperation.HTTPMethod.GET,
            path_template="/{resource_id}",
            input_schema={
                "type": "object",
                "required": ["resource_id"],
                "properties": {
                    "resource_id": {"type": "string"},
                },
            },
        )

        request = httpx.Request(
            "GET",
            "https://api.example.com/https://attacker.tld/steal",
        )
        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True},
            request=request,
        )
        with patch("nova.api_tools.service.safe_http_request", new=AsyncMock(return_value=response)) as mocked_request:
            result = async_to_sync(call_api_operation)(
                tool=self.tool,
                user=self.user,
                operation_selector=operation.slug,
                payload={"resource_id": "https://attacker.tld/steal"},
            )

        self.assertEqual(result["payload"]["response"]["json"]["ok"], True)
        self.assertEqual(
            mocked_request.await_args.args[:2],
            ("GET", "https://api.example.com/https://attacker.tld/steal"),
        )
