from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from nova.llm.tool_error_handling import (
    ToolErrorCategory,
    categorize_error,
    get_user_friendly_message,
    handle_tool_errors,
    retry_with_backoff,
)


class ToolErrorHandlingHelpersTests(SimpleTestCase):
    def test_categorize_error_detects_expected_categories(self):
        cases = [
            (TimeoutError("Request timeout"), ToolErrorCategory.TIMEOUT_ERROR),
            (
                RuntimeError("Page.goto: net::ERR_ABORTED at https://example.com/article"),
                ToolErrorCategory.BROWSER_ERROR,
            ),
            (ConnectionError("DNS lookup failed"), ToolErrorCategory.NETWORK_ERROR),
            (ValueError("Invalid schema: required field missing"), ToolErrorCategory.VALIDATION_ERROR),
            (RuntimeError("Rate limit exceeded"), ToolErrorCategory.RATE_LIMIT_ERROR),
            (PermissionError("Unauthorized credentials"), ToolErrorCategory.AUTHENTICATION_ERROR),
            (RuntimeError("API returned HTTP 500 status"), ToolErrorCategory.API_ERROR),
            (RuntimeError("Completely unrelated"), ToolErrorCategory.UNKNOWN_ERROR),
        ]

        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                self.assertEqual(categorize_error(error), expected)

    def test_get_user_friendly_message_uses_fallback_for_unknown_category(self):
        message = get_user_friendly_message(
            "made_up_category",
            "artifact_search",
            RuntimeError("boom"),
        )

        self.assertIn("artifact_search", message)
        self.assertIn("unexpected error", message.lower())

    def test_categorize_error_does_not_treat_plain_urls_as_api_errors(self):
        error = RuntimeError("Unexpected redirect while loading https://example.com/path")

        self.assertEqual(categorize_error(error), ToolErrorCategory.UNKNOWN_ERROR)


class RetryWithBackoffTests(IsolatedAsyncioTestCase):
    async def test_retry_with_backoff_retries_network_errors_until_success(self):
        handler = AsyncMock(
            side_effect=[
                ConnectionError("Network down"),
                ConnectionError("Network still down"),
                "done",
            ]
        )
        request = SimpleNamespace(tool_call={"name": "browser_search"})

        with patch("nova.llm.tool_error_handling.asyncio.sleep", new_callable=AsyncMock) as mocked_sleep:
            result = await retry_with_backoff(handler, request)

        self.assertEqual(result, "done")
        self.assertEqual(handler.await_count, 3)
        mocked_sleep.assert_has_awaits([((1,),), ((2,),)])

    async def test_retry_with_backoff_does_not_retry_validation_errors(self):
        handler = AsyncMock(side_effect=ValueError("Invalid schema"))
        request = SimpleNamespace(tool_call={"name": "calendar"})

        with patch("nova.llm.tool_error_handling.asyncio.sleep", new_callable=AsyncMock) as mocked_sleep:
            with self.assertRaises(ValueError):
                await retry_with_backoff(handler, request)

        self.assertEqual(handler.await_count, 1)
        mocked_sleep.assert_not_awaited()

    async def test_retry_with_backoff_raises_after_last_retry(self):
        handler = AsyncMock(side_effect=http_error_sequence("HTTP 503"))
        request = SimpleNamespace(tool_call={"name": "email_search"})

        with patch("nova.llm.tool_error_handling.asyncio.sleep", new_callable=AsyncMock) as mocked_sleep:
            with self.assertRaises(RuntimeError):
                await retry_with_backoff(handler, request)

        self.assertEqual(handler.await_count, 3)
        mocked_sleep.assert_has_awaits([((1,),), ((2,),)])


class HandleToolErrorsMiddlewareTests(IsolatedAsyncioTestCase):
    async def test_handle_tool_errors_returns_handler_result_on_success(self):
        expected = ToolMessage(content="ok", tool_call_id="call-1")
        handler = AsyncMock(return_value=expected)
        request = SimpleNamespace(tool_call={"name": "artifact_search", "id": "call-1"})

        result = await handle_tool_errors.awrap_tool_call(request, handler)

        self.assertIs(result, expected)
        handler.assert_awaited_once_with(request)

    async def test_handle_tool_errors_re_raises_graph_interrupt(self):
        interrupt = GraphInterrupt([Interrupt(value="pause", id="interrupt-1")])
        handler = AsyncMock(side_effect=interrupt)
        request = SimpleNamespace(tool_call={"name": "ask_user", "id": "call-graph"})

        with self.assertRaises(GraphInterrupt):
            await handle_tool_errors.awrap_tool_call(request, handler)

    async def test_handle_tool_errors_returns_user_friendly_message_for_non_retryable_errors(self):
        handler = AsyncMock(side_effect=ValueError("Invalid schema"))
        request = SimpleNamespace(tool_call={"name": "artifact_search", "id": "call-2"})

        result = await handle_tool_errors.awrap_tool_call(request, handler)

        self.assertIsInstance(result, ToolMessage)
        self.assertIn("Invalid input provided to artifact_search", result.content)
        self.assertEqual(result.tool_call_id, "call-2")
        self.assertEqual(
            result.additional_kwargs["error_category"],
            ToolErrorCategory.VALIDATION_ERROR,
        )
        self.assertEqual(result.additional_kwargs["original_error"], "Invalid schema")

    async def test_handle_tool_errors_returns_retry_result_for_network_errors(self):
        request = SimpleNamespace(tool_call={"name": "browser_search", "id": "call-3"})
        handler = AsyncMock(side_effect=ConnectionError("Network down"))
        retry_result = ToolMessage(content="retried", tool_call_id="call-3")

        with patch(
            "nova.llm.tool_error_handling.retry_with_backoff",
            new_callable=AsyncMock,
            return_value=retry_result,
        ) as mocked_retry:
            result = await handle_tool_errors.awrap_tool_call(request, handler)

        self.assertIs(result, retry_result)
        mocked_retry.assert_awaited_once_with(handler, request)

    async def test_handle_tool_errors_falls_back_to_error_message_when_retry_fails(self):
        request = SimpleNamespace(tool_call={"name": "web_search", "id": "call-4"})
        handler = AsyncMock(side_effect=ConnectionError("DNS failure"))

        with patch(
            "nova.llm.tool_error_handling.retry_with_backoff",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Still failing"),
        ) as mocked_retry:
            result = await handle_tool_errors.awrap_tool_call(request, handler)

        self.assertIsInstance(result, ToolMessage)
        self.assertIn("Network connectivity issue while using web_search", result.content)
        self.assertEqual(
            result.additional_kwargs["error_category"],
            ToolErrorCategory.NETWORK_ERROR,
        )
        mocked_retry.assert_awaited_once_with(handler, request)

    async def test_handle_tool_errors_does_not_retry_browser_abort_errors(self):
        request = SimpleNamespace(tool_call={"name": "navigate_browser", "id": "call-5"})
        handler = AsyncMock(side_effect=RuntimeError("Page.goto: net::ERR_ABORTED at https://example.com"))

        with patch(
            "nova.llm.tool_error_handling.retry_with_backoff",
            new_callable=AsyncMock,
        ) as mocked_retry:
            result = await handle_tool_errors.awrap_tool_call(request, handler)

        self.assertIsInstance(result, ToolMessage)
        self.assertIn("could not finish loading the page", result.content)
        self.assertEqual(
            result.additional_kwargs["error_category"],
            ToolErrorCategory.BROWSER_ERROR,
        )
        mocked_retry.assert_not_awaited()


def http_error_sequence(message: str):
    return [RuntimeError(message), RuntimeError(message), RuntimeError(message)]
