import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from django.core.cache import cache
from django.http import Http404
from django.test import SimpleTestCase

import httpx

from nova.mcp.client import MCPClient


class MCPClientTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    # ------------- helpers -----------------

    class _FakeAsyncClient:
        def __init__(self, tools_queue=None, call_results_queue=None,
                     raise_on_call=None):
            self.tools_queue = tools_queue or []
            self.call_results_queue = call_results_queue or []
            self.raise_on_call = raise_on_call
            self.list_tools_calls = 0
            self.call_tool_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def list_tools(self):
            self.list_tools_calls += 1
            if self.tools_queue:
                return self.tools_queue.pop(0)
            return []

        async def call_tool(self, tool_name, inputs):
            self.call_tool_calls += 1
            if self.raise_on_call:
                raise self.raise_on_call
            if self.call_results_queue:
                return self.call_results_queue.pop(0)
            return {"ok": True, "tool": tool_name, "inputs": inputs}

    # ------------- alist_tools -----------------

    def test_alist_tools_maps_fields_and_caches(self):
        # Prepare fake tools with mixed attribute styles
        class T1:
            name = "t1"
            description = "desc1"
            input_schema = {"a": 1}
            output_schema = {"b": 2}

        class T2:
            name = "t2"
            description = "desc2"
            inputSchema = {"x": 1}
            outputSchema = {"y": 2}

        fake_client = self._FakeAsyncClient(tools_queue=[[T1(), T2()]])
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c = MCPClient(endpoint="https://srv.example.com", user_id=123)
            # First call: not cached
            tools = asyncio.run(c.alist_tools())
            self.assertEqual(fake_client.list_tools_calls, 1)
            self.assertEqual(
                tools,
                [
                    {"name": "t1", "description": "desc1",
                     "input_schema": {"a": 1}, "output_schema": {"b": 2}},
                    {"name": "t2", "description": "desc2",
                     "input_schema": {"x": 1}, "output_schema": {"y": 2}},
                ],
            )
            # Second call: cached
            tools2 = asyncio.run(c.alist_tools())
            self.assertEqual(fake_client.list_tools_calls, 1)
            self.assertEqual(tools2, tools)

    def test_alist_tools_cache_is_isolated_by_user_and_force_refresh(self):
        # Build lightweight tool objects with only the required attributes
        class ToolObj:
            def __init__(self, name):
                self.name = name
                # no description/input_schema/output_schema -> defaults applied

        list_a_objs = [ToolObj("a")]
        list_b_objs = [ToolObj("b")]
        list_c_objs = [ToolObj("c")]

        fake_client = self._FakeAsyncClient(
            tools_queue=[list_a_objs, list_b_objs, list_c_objs]
        )
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c1 = MCPClient(endpoint="http://srv", user_id=1)
            c2 = MCPClient(endpoint="http://srv", user_id=2)

            out1 = asyncio.run(c1.alist_tools())
            out2 = asyncio.run(c2.alist_tools())
            self.assertEqual(fake_client.list_tools_calls, 2)
            self.assertEqual(out1, [{"name": "a", "description": "",
                                     "input_schema": {}, "output_schema": {}}])
            self.assertEqual(out2, [{"name": "b", "description": "",
                                     "input_schema": {}, "output_schema": {}}])

            # Same user, no refresh -> cache
            out1b = asyncio.run(c1.alist_tools())
            self.assertEqual(fake_client.list_tools_calls, 2)
            self.assertEqual(out1b, [{"name": "a", "description": "",
                                      "input_schema": {},
                                      "output_schema": {}}])

            # Force refresh -> new call returns list_c
            out1c = asyncio.run(c1.alist_tools(force_refresh=True))
            self.assertEqual(fake_client.list_tools_calls, 3)
            self.assertEqual(out1c, [{"name": "c", "description": "",
                                      "input_schema": {},
                                      "output_schema": {}}])

    # ------------- acall -----------------

    def test_acall_caches_by_tool_and_normalized_inputs(self):
        result1 = {"ok": 1}
        fake_client = self._FakeAsyncClient(call_results_queue=[result1])
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c = MCPClient(endpoint="https://x", user_id=42)

            # First call populates cache
            out1 = asyncio.run(c.acall("sum", a=1, b=2))
            self.assertEqual(out1, result1)
            self.assertEqual(fake_client.call_tool_calls, 1)

            # Second call with kwargs reversed should hit cache, not call again
            out2 = asyncio.run(c.acall("sum", b=2, a=1))
            self.assertEqual(out2, result1)
            self.assertEqual(fake_client.call_tool_calls, 1)

            # Optional sanity on cache key structure
            base = json.dumps(("sum", [("a", 1), ("b", 2)]),
                              sort_keys=True).encode("utf-8")
            _ = base64.urlsafe_b64encode(base).decode("utf-8")

    def test_acall_http_errors_are_mapped(self):
        req = httpx.Request("GET", "http://x")
        resp_404 = httpx.Response(404, request=req)
        resp_500 = httpx.Response(500, request=req)
        err_404 = httpx.HTTPStatusError("not found", request=req,
                                        response=resp_404)
        err_500 = httpx.HTTPStatusError("server err", request=req,
                                        response=resp_500)

        # 404 -> Http404
        fake_client_404 = self._FakeAsyncClient(raise_on_call=err_404)
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client_404), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c = MCPClient(endpoint="http://x")
            with self.assertRaises(Http404):
                asyncio.run(c.acall("any", x=1))

        # 500 -> re-raised HTTPStatusError
        fake_client_500 = self._FakeAsyncClient(raise_on_call=err_500)
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client_500), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c = MCPClient(endpoint="http://x")
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(c.acall("any", x=1))

        # RequestError -> ConnectionError
        err_conn = httpx.RequestError("boom", request=req)
        fake_client_conn = self._FakeAsyncClient(raise_on_call=err_conn)
        with patch("nova.mcp.client.FastMCPClient",
                   lambda transport: fake_client_conn), \
             patch.object(MCPClient, "_transport", return_value=object()):
            c = MCPClient(endpoint="http://x")
            with self.assertRaises(ConnectionError):
                asyncio.run(c.acall("any", x=1))

    # ------------- _validate_inputs -----------------

    def test_validate_inputs_accepts_supported_and_rejects_others(self):
        c = MCPClient(endpoint="http://srv")

        # Accepted types and nesting up to depth 5
        ok_inputs = {
            "s": "hello",
            "i": 1,
            "f": 1.2,
            "b": True,
            "n": None,
            "d": {"k": "v", "l": [1, 2, {"z": 3}]},
        }
        c._validate_inputs(ok_inputs)  # should not raise

        # Unsupported type (set)
        with self.assertRaises(Exception):
            c._validate_inputs({"bad": {1, 2, 3}})

        # Excessive depth (>5) - ensure depth actually reaches 6
        too_deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}
        with self.assertRaises(Exception):
            c._validate_inputs(too_deep)

        # Too long string (>2048)
        long_str = "x" * 2049
        with self.assertRaises(Exception):
            c._validate_inputs({"s": long_str})

    # ------------- sync wrappers -----------------

    def test_list_tools_sync_wrapper_calls_async(self):
        with patch.object(MCPClient, "alist_tools",
                          new=AsyncMock(return_value=["ok"])) as mocked:
            c = MCPClient(endpoint="http://srv")
            out = asyncio.run(c.alist_tools())
            self.assertEqual(out, ["ok"])
            mocked.assert_called_once_with()

    def test_call_sync_wrapper_validates_then_calls_async(self):
        with patch.object(MCPClient, "_validate_inputs") as validate_mock, \
             patch.object(MCPClient, "acall",
                          new=AsyncMock(return_value="OK")) as acall_mock:
            c = MCPClient(endpoint="http://srv")
            out = c.call("t", x=1)
            self.assertEqual(out, "OK")
            validate_mock.assert_called_once()
            acall_mock.assert_awaited_once_with("t", x=1)

    # ------------- auth helper -----------------

    def test_auth_object_uses_bearer_when_token_like(self):
        class FakeAuth:
            def __init__(self, token):
                self.token = token

        with patch("nova.mcp.client.BearerAuth", FakeAuth):
            cred = SimpleNamespace(auth_type="token", token="ABC")
            c = MCPClient(endpoint="http://srv", credential=cred)
            auth = c._auth_object()
            self.assertIsInstance(auth, FakeAuth)
            self.assertEqual(auth.token, "ABC")

            cred2 = SimpleNamespace(auth_type="none", token=None)
            c2 = MCPClient(endpoint="http://srv", credential=cred2)
            self.assertIsNone(c2._auth_object())

            cred3 = SimpleNamespace(auth_type="token", token=None)
            c3 = MCPClient(endpoint="http://srv", credential=cred3)
            self.assertIsNone(c3._auth_object())
