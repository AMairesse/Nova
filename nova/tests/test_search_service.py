from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import TransactionTestCase

from nova.models.Tool import Tool, ToolCredential
from nova.plugins.search.service import test_searxng_access as check_searxng_access
from nova.web.search_service import get_searxng_config, search_web


class SearxngSearchServiceTests(TransactionTestCase):
    def _create_searxng_tool(self, *, user=None, url: str = "http://searxng:8080") -> Tool:
        tool = Tool.objects.create(
            user=user,
            name="SearXNG",
            description="Search backend",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.plugins.search",
        )
        ToolCredential.objects.create(
            user=user,
            tool=tool,
            auth_type="none",
            config={"searxng_url": url, "num_results": 5},
        )
        return tool

    def test_system_searxng_config_allows_configured_single_label_host(self):
        tool = self._create_searxng_tool()

        config = async_to_sync(get_searxng_config)(tool)

        self.assertEqual(config["endpoint"], "http://searxng:8080/search")
        self.assertEqual(config["allowed_private_hosts"], ("searxng",))

    def test_user_searxng_config_does_not_implicitly_allow_private_host(self):
        user = User.objects.create_user(username="search-user")
        tool = self._create_searxng_tool(user=user)

        config = async_to_sync(get_searxng_config)(tool)

        self.assertEqual(config["allowed_private_hosts"], ())

    def test_search_web_forwards_system_allowed_private_hosts(self):
        tool = self._create_searxng_tool()
        response = httpx.Response(
            200,
            json={"results": [{"title": "Nova", "url": "https://example.com", "content": "Docs"}]},
            request=httpx.Request("GET", "http://searxng:8080/search"),
        )

        with patch(
            "nova.web.search_service.safe_http_request",
            new_callable=AsyncMock,
            return_value=response,
        ) as mocked_request:
            payload = async_to_sync(search_web)(tool, "nova")

        self.assertEqual(payload["results"][0]["title"], "Nova")
        self.assertEqual(mocked_request.await_args.kwargs["allowed_private_hosts"], ("searxng",))

    def test_connection_check_forwards_system_allowed_private_hosts(self):
        tool = self._create_searxng_tool()
        response = httpx.Response(
            200,
            json={"results": [{"title": "Nova"}]},
            request=httpx.Request("GET", "http://searxng:8080/search"),
        )

        with patch(
            "nova.plugins.search.service.safe_http_request",
            new_callable=AsyncMock,
            return_value=response,
        ) as mocked_request:
            result = async_to_sync(check_searxng_access)(tool)

        self.assertEqual(result["status"], "success")
        self.assertEqual(mocked_request.await_args.kwargs["allowed_private_hosts"], ("searxng",))
