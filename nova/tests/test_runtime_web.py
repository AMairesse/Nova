import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import (
    BROWSER_DEFAULT_ELEMENT_ATTRIBUTES,
    BROWSER_SINGLE_PANE_ERROR,
    TerminalCommandError,
)
from nova.web.browser_service import BrowserSessionError

from .runtime_command_base import _FakeBrowserSession, TerminalExecutorCommandTestCase


class WebCommandTests(TerminalExecutorCommandTestCase):
    def test_curl_accepts_common_user_agent_header_and_output_flags(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )
        captured = {}

        async def fake_download_http_file(url, *, headers=None, user_agent="", filename="", max_size=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["user_agent"] = user_agent
            captured["filename"] = filename
            captured["max_size"] = max_size
            return {
                "url": url,
                "filename": "trentemoult-real-street.jpg",
                "mime_type": "image/jpeg",
                "content": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
                "size": 10,
            }

        with patch("nova.runtime.terminal.download_http_file", new=fake_download_http_file):
            output = async_to_sync(executor.execute)(
                'curl -A "Mozilla/5.0" -H "Referer: https://example.com" -o /tmp/trentemoult-real-street.jpg '
                '"https://upload.wikimedia.org/wikipedia/commons/a/a5/Rue_de_la_Biscuiterie.jpg"'
            )

        self.assertIn("/tmp/trentemoult-real-street.jpg", output)
        self.assertEqual(
            captured["url"],
            "https://upload.wikimedia.org/wikipedia/commons/a/a5/Rue_de_la_Biscuiterie.jpg",
        )
        self.assertEqual(captured["user_agent"], "Mozilla/5.0")
        self.assertEqual(captured["headers"]["Referer"], "https://example.com")

    def test_curl_accepts_url_after_options_and_multiple_headers(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )
        captured = {}

        async def fake_download_http_file(url, *, headers=None, user_agent="", filename="", max_size=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["user_agent"] = user_agent
            del filename, max_size
            return {
                "url": url,
                "filename": "page.html",
                "mime_type": "text/html",
                "content": b"<html>Hello</html>",
                "size": 18,
            }

        with patch("nova.runtime.terminal.download_http_file", new=fake_download_http_file):
            output = async_to_sync(executor.execute)(
                'curl -H "Referer: https://example.com" -H "User-Agent: BrowserUA/1.0" https://example.com/page.html'
            )

        self.assertIn("<html>Hello</html>", output)
        self.assertEqual(captured["url"], "https://example.com/page.html")
        self.assertEqual(captured["headers"]["Referer"], "https://example.com")
        self.assertEqual(captured["user_agent"], "BrowserUA/1.0")

    def test_wget_accepts_common_user_agent_and_header_flags(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )
        captured = {}

        async def fake_download_http_file(url, *, headers=None, user_agent="", filename="", max_size=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["user_agent"] = user_agent
            del filename, max_size
            return {
                "url": url,
                "filename": "trentemoult.jpg",
                "mime_type": "image/jpeg",
                "content": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
                "size": 10,
            }

        with patch("nova.runtime.terminal.download_http_file", new=fake_download_http_file):
            output = async_to_sync(executor.execute)(
                'wget -U "Mozilla/5.0" --header "Referer: https://example.com" -O /tmp/trentemoult.jpg '
                'https://upload.wikimedia.org/wikipedia/commons/a/a5/Rue_de_la_Biscuiterie.jpg'
            )

        self.assertIn("/tmp/trentemoult.jpg", output)
        self.assertEqual(
            captured["url"],
            "https://upload.wikimedia.org/wikipedia/commons/a/a5/Rue_de_la_Biscuiterie.jpg",
        )
        self.assertEqual(captured["user_agent"], "Mozilla/5.0")
        self.assertEqual(captured["headers"]["Referer"], "https://example.com")

    def test_curl_rejects_invalid_header_value(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )

        with self.assertRaisesRegex(TerminalCommandError, "expected 'Name: value'"):
            async_to_sync(executor.execute)('curl -H "BrokenHeader" https://example.com')

    def test_curl_rejects_multiple_urls(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )

        with self.assertRaisesRegex(TerminalCommandError, "single URL"):
            async_to_sync(executor.execute)("curl https://example.com https://example.org")

    def test_curl_rejects_unknown_flags(self):
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=SimpleNamespace()),
        )

        with self.assertRaisesRegex(TerminalCommandError, "Unsupported curl flag"):
            async_to_sync(executor.execute)("curl --compressed https://example.com")

    def test_search_command_formats_results_and_supports_output(self):
        searxng_tool = self._create_searxng_tool()
        executor = self._build_executor(
            TerminalCapabilities(searxng_tool=searxng_tool)
        )

        with patch(
            "nova.runtime.commands.web.search_web",
            new_callable=AsyncMock,
            return_value={
                "query": "nova privacy",
                "results": [
                    {
                        "title": "Nova docs",
                        "url": "https://example.com/nova",
                        "snippet": "Privacy-first agent platform",
                        "engine": "searx",
                        "score": 0.9,
                    }
                ],
                "limit": 1,
            },
        ) as mocked_search:
            listing = async_to_sync(executor.execute)("search nova privacy --limit 1")
            written = async_to_sync(executor.execute)(
                "search nova privacy --limit 1 --output /search/results.json"
            )

        self.assertIn("0. Nova docs / https://example.com/nova / Privacy-first agent platform", listing)
        self.assertIn("/search/results.json", written)
        stored = async_to_sync(executor.execute)("cat /search/results.json")
        self.assertEqual(json.loads(stored)["query"], "nova privacy")
        self.assertEqual(mocked_search.await_args.kwargs["limit"], 1)

    def test_browse_open_result_requires_search_and_browse_session_is_run_local(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        searxng_tool = self._create_searxng_tool()
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool, searxng_tool=searxng_tool)
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("browse open --result 0")
        executor._browser_session = None

        fake_session = _FakeBrowserSession()
        with (
            patch(
                "nova.runtime.commands.web.search_web",
                new_callable=AsyncMock,
                return_value={
                    "query": "nova",
                    "results": [
                        {
                            "title": "Nova docs",
                            "url": "https://example.com/nova",
                            "snippet": "Docs",
                            "engine": "searx",
                            "score": None,
                        }
                    ],
                    "limit": 1,
                },
            ),
            patch("nova.runtime.terminal.BrowserSession", return_value=fake_session),
        ):
            search_result = async_to_sync(executor.execute)("search nova")
            opened = async_to_sync(executor.execute)("browse open --result 0")
            current = async_to_sync(executor.execute)("browse current")

        self.assertIn("Nova docs", search_result)
        self.assertIn("0. Nova docs / https://example.com/nova / Docs", search_result)
        self.assertIn("https://example.com/result", opened)
        self.assertEqual(current, "https://example.com/result")
        fake_session.open_search_result.assert_awaited_once()
        self.assertEqual(fake_session.open_search_result.await_args.args[0], 0)

        with (
            patch(
                "nova.runtime.commands.web.search_web",
                new_callable=AsyncMock,
                return_value={
                    "query": "nova",
                    "results": [
                        {
                            "title": "Nova docs",
                            "url": "https://example.com/nova",
                            "snippet": "Docs",
                            "engine": "searx",
                            "score": None,
                        }
                    ],
                    "limit": 1,
                },
            ),
            patch(
                "nova.runtime.terminal.BrowserSession",
                return_value=SimpleNamespace(
                    open_search_result=AsyncMock(
                        side_effect=BrowserSessionError(
                            "Search result 1 is out of range. Available range: 0..0."
                        )
                    )
                ),
            ),
        ):
            executor._browser_session = None
            async_to_sync(executor.execute)("search nova")
            with self.assertRaisesRegex(TerminalCommandError, r"Available range: 0\.\.0"):
                async_to_sync(executor.execute)("browse open --result 1")

        next_run_executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fresh_session = _FakeBrowserSession()
        fresh_session.current = AsyncMock(
            side_effect=BrowserSessionError(
                "No active page in the current browser session. Use `browse open` first."
            )
        )
        with patch(
            "nova.runtime.terminal.BrowserSession",
            return_value=fresh_session,
        ):
            with self.assertRaises(TerminalCommandError):
                async_to_sync(next_run_executor.execute)("browse current")

    def test_browse_text_links_elements_and_click_support_output(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            opened = async_to_sync(executor.execute)("browse open https://example.com")
            text_preview = async_to_sync(executor.execute)("browse text")
            text_written = async_to_sync(executor.execute)("browse text --output /page.txt")
            links_preview = async_to_sync(executor.execute)("browse links --absolute")
            links_written = async_to_sync(executor.execute)("browse links --absolute --output /links.json")
            elements_preview = async_to_sync(executor.execute)(
                'browse elements "a" --attr href --attr innerText'
            )
            elements_written = async_to_sync(executor.execute)(
                'browse elements "a" --attr href --attr innerText --output /elements.json'
            )
            clicked = async_to_sync(executor.execute)('browse click "a.link"')

        self.assertIn("Opened https://example.com", opened)
        self.assertIn("Page text", text_preview)
        self.assertIn("/page.txt", text_written)
        self.assertIn("https://example.com/a", links_preview)
        self.assertIn("/links.json", links_written)
        self.assertIn('"selector": "a"', elements_preview)
        self.assertIn("/elements.json", elements_written)
        self.assertIn("Clicked element", clicked)
        self.assertIn("Page text", async_to_sync(executor.execute)("cat /page.txt"))
        self.assertEqual(json.loads(async_to_sync(executor.execute)("cat /links.json"))[0]["href"], "https://example.com/a")
        self.assertEqual(json.loads(async_to_sync(executor.execute)("cat /elements.json"))["selector"], "a")

    def test_browse_read_alias_inline_url_and_pane_zero_work(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            read_preview = async_to_sync(executor.execute)("browse read https://example.com/gallery")
            links_preview = async_to_sync(executor.execute)("browse links https://example.com/list --absolute")
            async_to_sync(executor.execute)("browse open https://example.com/current")
            current = async_to_sync(executor.execute)("browse current --pane 0")
            clicked = async_to_sync(executor.execute)('browse click "a.link" --pane 0')

        self.assertIn("Page text", read_preview)
        self.assertIn("https://example.com/a", links_preview)
        self.assertEqual(fake_session.open.await_args_list[0].args[0], "https://example.com/gallery")
        self.assertEqual(fake_session.open.await_args_list[1].args[0], "https://example.com/list")
        self.assertEqual(current, "https://example.com/result")
        self.assertIn("Clicked element", clicked)

    def test_browse_ls_lists_current_page_and_supports_pane_zero(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            async_to_sync(executor.execute)("browse open https://example.com/current")
            listed = async_to_sync(executor.execute)("browse ls")
            listed_with_pane = async_to_sync(executor.execute)("browse ls --pane 0")

        self.assertEqual(listed, "0  current  https://example.com/result")
        self.assertEqual(listed_with_pane, "0  current  https://example.com/result")

    def test_browse_ls_supports_shell_redirection(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            async_to_sync(executor.execute)("browse open https://example.com/current")
            written = async_to_sync(executor.execute)("browse ls > /page.txt")

        stored = async_to_sync(executor.execute)("cat /page.txt")
        self.assertIn("/page.txt", written)
        self.assertEqual(stored, "0  current  https://example.com/result")

    def test_browse_pane_one_or_more_is_rejected_with_clear_error(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )

        with self.assertRaisesRegex(TerminalCommandError, re.escape(BROWSER_SINGLE_PANE_ERROR)):
            async_to_sync(executor.execute)("browse read --pane 1")
        with self.assertRaisesRegex(TerminalCommandError, re.escape(BROWSER_SINGLE_PANE_ERROR)):
            async_to_sync(executor.execute)('browse elements "img" --pane 2')
        with self.assertRaisesRegex(TerminalCommandError, re.escape(BROWSER_SINGLE_PANE_ERROR)):
            async_to_sync(executor.execute)("browse ls --pane 1")

    def test_browse_elements_inline_url_uses_default_useful_attributes(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()
        fake_session.get_elements = AsyncMock(
            return_value=[{"tagName": "img", "src": "https://example.com/image.png", "alt": "Preview"}]
        )

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            written = async_to_sync(executor.execute)(
                'browse elements "img" https://example.com/gallery --output /images.json'
            )

        payload = json.loads(async_to_sync(executor.execute)("cat /images.json"))
        self.assertIn("/images.json", written)
        self.assertEqual(fake_session.open.await_args.args[0], "https://example.com/gallery")
        self.assertEqual(fake_session.get_elements.await_args.args[0], "img")
        self.assertEqual(fake_session.get_elements.await_args.args[1], list(BROWSER_DEFAULT_ELEMENT_ATTRIBUTES))
        self.assertEqual(payload["selector"], "img")
        self.assertEqual(payload["elements"][0]["tagName"], "img")
        self.assertEqual(payload["elements"][0]["src"], "https://example.com/image.png")

    def test_browse_text_without_active_page_suggests_open_or_inline_url(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fresh_session = _FakeBrowserSession()
        fresh_session.extract_text = AsyncMock(
            side_effect=BrowserSessionError(
                "No active page in the current browser session. Use `browse open` first."
            )
        )

        with patch("nova.runtime.terminal.BrowserSession", return_value=fresh_session):
            with self.assertRaisesRegex(TerminalCommandError, r"browse open <url>"):
                async_to_sync(executor.execute)("browse text")
            with self.assertRaisesRegex(TerminalCommandError, r"browse text <url>"):
                async_to_sync(executor.execute)("browse text")

    def test_browse_ls_without_active_page_matches_browse_current_error(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fresh_session = _FakeBrowserSession()
        fresh_session.current = AsyncMock(
            side_effect=BrowserSessionError(
                "No active page in the current browser session. Use `browse open` first."
            )
        )

        with patch("nova.runtime.terminal.BrowserSession", return_value=fresh_session):
            with self.assertRaisesRegex(TerminalCommandError, r"No active page in the current browser session"):
                async_to_sync(executor.execute)("browse ls")

    def test_browse_text_supports_shell_redirection(self):
        browser_tool = self._create_builtin_tool("browser", name="Browser")
        executor = self._build_executor(
            TerminalCapabilities(browser_tool=browser_tool)
        )
        fake_session = _FakeBrowserSession()
        fake_session.extract_text = AsyncMock(return_value="A" * 9005)

        with patch("nova.runtime.terminal.BrowserSession", return_value=fake_session):
            async_to_sync(executor.execute)("browse open https://example.com")
            written = async_to_sync(executor.execute)("browse text > /page.txt")

        stored = async_to_sync(executor.execute)("cat /page.txt")
        self.assertIn("/page.txt", written)
        self.assertEqual(len(stored), 9005)
