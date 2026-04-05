from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.tests.factories import create_tool, create_tool_credential, create_user
from nova.tools.builtins import searxng


class SearXNGBuiltinsTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="searx-user", email="searx@example.com")
        self.tool = create_tool(
            self.user,
            name="SearXNG",
            tool_subtype="searxng",
            python_path="nova.tools.builtins.searxng",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "searxng_url": "https://search.example.com",
                "num_results": 5,
            },
        )

    @patch("nova.tools.builtins.searxng.search_web", new_callable=AsyncMock)
    def test_get_functions_returns_native_search_tool(self, mocked_search):
        mocked_search.return_value = {
            "query": "nova privacy",
            "results": [
                {
                    "title": "Nova",
                    "url": "https://example.com/nova",
                    "snippet": "Privacy-first",
                    "engine": "searx",
                    "score": 0.9,
                }
            ],
            "limit": 1,
        }
        agent = type("Agent", (), {"user": self.user})()

        tools = asyncio.run(searxng.get_functions(self.tool, agent))
        self.assertEqual([tool.name for tool in tools], ["searx_search_results"])

        payload = asyncio.run(tools[0].coroutine(query="nova privacy"))
        decoded = json.loads(payload)

        self.assertEqual(decoded[0]["url"], "https://example.com/nova")
        self.assertEqual(mocked_search.await_args.kwargs["query"], "nova privacy")
