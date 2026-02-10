from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.tests.factories import create_tool, create_tool_credential, create_user
from nova.tools.builtins import code_execution


class CodeExecutionBuiltinsTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="judge-user", email="judge@example.com")
        self.tool = create_tool(
            self.user,
            name="Judge0",
            tool_subtype="code_execution",
            python_path="nova.tools.builtins.code_execution",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "judge0_url": "https://judge.example.com/",
                "timeout": 7,
            },
        )
        code_execution._languages_cache = None

    def test_get_judge0_host_requires_credential(self):
        other_tool = create_tool(
            self.user,
            name="Judge0 missing",
            tool_subtype="code_execution",
            python_path="nova.tools.builtins.code_execution",
        )
        with self.assertRaisesMessage(ValueError, "No credential configured"):
            asyncio.run(code_execution.get_judge0_host(other_tool))

    @patch("nova.tools.builtins.code_execution.api_request", new_callable=AsyncMock)
    def test_fetch_languages_uses_cache(self, mocked_api_request):
        mocked_api_request.return_value = [{"id": 71, "name": "Python 3.8"}]

        first = asyncio.run(code_execution.fetch_languages("https://judge.example.com"))
        second = asyncio.run(code_execution.fetch_languages("https://judge.example.com"))

        self.assertEqual(first, second)
        mocked_api_request.assert_awaited_once()

    @patch("nova.tools.builtins.code_execution.fetch_languages", new_callable=AsyncMock)
    def test_get_language_id_fuzzy_match_and_not_found(self, mocked_fetch_languages):
        mocked_fetch_languages.return_value = [
            {"id": 70, "name": "Python 2"},
            {"id": 71, "name": "Python 3.8"},
            {"id": 63, "name": "JavaScript"},
        ]

        lang_id = asyncio.run(code_execution.get_language_id("https://judge.example.com", "python"))
        self.assertEqual(lang_id, 71)

        with self.assertRaisesMessage(ValueError, "Language 'rust' not found"):
            asyncio.run(code_execution.get_language_id("https://judge.example.com", "rust"))

    @patch("nova.tools.builtins.code_execution.api_request", new_callable=AsyncMock)
    @patch("nova.tools.builtins.code_execution.get_language_id", new_callable=AsyncMock, return_value=71)
    def test_execute_code_decodes_stdout_and_stderr(self, mocked_get_language, mocked_api_request):
        mocked_api_request.return_value = {
            "stdout": base64.b64encode(b"hello\n").decode("utf-8"),
            "stderr": base64.b64encode(b"").decode("utf-8"),
            "status": {"description": "Accepted"},
        }

        output = asyncio.run(
            code_execution.execute_code(
                host="https://judge.example.com",
                code="print('hello')",
                language="python",
            )
        )

        self.assertIn("Status: Accepted", output)
        self.assertIn("Stdout: hello", output)
        mocked_get_language.assert_awaited_once()
        mocked_api_request.assert_awaited_once()

    @patch("nova.tools.builtins.code_execution.asyncio.sleep", new_callable=AsyncMock)
    @patch("nova.tools.builtins.code_execution.get_execution_status", new_callable=AsyncMock, return_value="Status: In Queue")
    @patch("nova.tools.builtins.code_execution.api_request", new_callable=AsyncMock, return_value={"token": "abc"})
    @patch("nova.tools.builtins.code_execution.get_language_id", new_callable=AsyncMock, return_value=71)
    def test_compile_code_times_out_when_status_never_completes(
        self,
        mocked_get_language,
        mocked_api_request,
        mocked_get_status,
        mocked_sleep,
    ):
        result = asyncio.run(
            code_execution.compile_code(
                host="https://judge.example.com",
                code="print('x')",
                language="python",
            )
        )

        self.assertEqual(result, "Compilation timeout")
        self.assertEqual(mocked_get_status.await_count, 10)
        self.assertEqual(mocked_sleep.await_count, 10)
        mocked_get_language.assert_awaited_once()
        mocked_api_request.assert_awaited_once()

    def test_get_functions_returns_expected_tools(self):
        tools = asyncio.run(code_execution.get_functions(self.tool, agent=None))
        names = [tool.name for tool in tools]

        self.assertEqual(
            names,
            ["list_supported_languages", "execute_code", "compile_code", "run_code_with_input"],
        )
