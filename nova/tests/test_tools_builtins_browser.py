from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase

from nova.tests.factories import create_tool, create_user
from nova.tools.builtins import browser


class _FakeStreamResponse:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]):
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.last_method = None
        self.last_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method: str, url: str):
        self.last_method = method
        self.last_url = url
        return _FakeStreamResponse(
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="report.pdf"',
            },
            chunks=[b"%PDF", b"-1.4"],
        )


class BrowserBuiltinsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="browser-user", email="browser@example.com")
        self.tool = create_tool(
            self.user,
            name="Browser",
            tool_subtype="browser",
            python_path="nova.tools.builtins.browser",
        )

    def test_get_prompt_instructions_mentions_download_tool(self):
        instructions = browser.get_prompt_instructions()

        self.assertTrue(any("web_download_file" in line for line in instructions))

    @patch("nova.tools.builtins.browser.stage_external_files_as_artifacts", new_callable=AsyncMock)
    @patch("nova.web.download_service.httpx.AsyncClient", new=_FakeAsyncClient)
    def test_web_download_file_returns_artifact_payload(self, mocked_stage_artifacts):
        mocked_stage_artifacts.return_value = (
            [
                SimpleNamespace(
                    id=31,
                    kind="pdf",
                    filename="report.pdf",
                    mime_type="application/pdf",
                )
            ],
            [],
        )
        agent = SimpleNamespace(user=self.user, thread=SimpleNamespace(id=9))

        message, payload = asyncio.run(
            browser.web_download_file(agent, "https://example.com/files/report")
        )

        self.assertIn("Downloaded file report.pdf", message)
        self.assertEqual(payload["artifact_refs"][0]["artifact_id"], 31)
        staged_spec = mocked_stage_artifacts.await_args.args[1][0]
        self.assertEqual(staged_spec["filename"], "report.pdf")
        self.assertEqual(staged_spec["mime_type"], "application/pdf")
        self.assertEqual(staged_spec["origin_locator"], {"url": "https://example.com/files/report"})
        self.assertEqual(staged_spec["content"], b"%PDF-1.4")

    @patch("nova.tools.builtins.browser.PlayWrightBrowserToolkit.from_browser")
    def test_get_functions_appends_download_tool(self, mocked_from_browser):
        mocked_from_browser.return_value = SimpleNamespace(
            get_tools=lambda: [SimpleNamespace(name="browser_navigate")]
        )
        agent = SimpleNamespace(_resources={"browser": object()})

        tools = asyncio.run(browser.get_functions(self.tool, agent))
        names = [tool.name for tool in tools]

        self.assertEqual(names, ["browser_navigate", "web_download_file"])
