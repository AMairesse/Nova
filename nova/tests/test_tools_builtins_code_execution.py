from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from nova.exec_runner.service import ExecRunnerError
from nova.exec_runner.service import SandboxShellResult
from nova.plugins.python import service as python_service


class PythonBuiltinsTests(SimpleTestCase):
    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service.httpx.AsyncClient.request", new_callable=AsyncMock)
    def test_test_exec_runner_access_reports_success_when_enabled(self, mocked_request):
        mocked_request.return_value = SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})
        result = asyncio.run(python_service.test_exec_runner_access())

        self.assertEqual(result["status"], "success")
        self.assertIn("exec-runner", result["message"])

    @override_settings(EXEC_RUNNER_ENABLED=False)
    def test_test_exec_runner_access_reports_error_when_disabled(self):
        result = asyncio.run(python_service.test_exec_runner_access())

        self.assertEqual(result["status"], "error")
        self.assertIn("disabled", result["message"])

    @override_settings(EXEC_RUNNER_ENABLED=True, EXEC_RUNNER_BASE_URL="", EXEC_RUNNER_SHARED_TOKEN="")
    def test_test_exec_runner_access_reports_error_when_runner_is_not_configured(self):
        result = asyncio.run(python_service.test_exec_runner_access())

        self.assertEqual(result["status"], "error")
        self.assertIn("not configured", result["message"])

    def test_execute_python_request_requires_runtime_context(self):
        with self.assertRaises(ExecRunnerError):
            asyncio.run(
                python_service.execute_python_request(
                    "",
                    python_service.PythonExecutionRequest(
                        code="print('hello from python')",
                        mode="inline",
                    ),
                )
            )

    @patch("nova.plugins.python.service.exec_runner_service.execute_workspace_python_command", new_callable=AsyncMock)
    def test_execute_python_request_uses_runtime_context_and_collects_synced_files(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(
                stdout="done\n",
                stderr="",
                status=0,
                cwd_after="/project",
            ),
            (
                SimpleNamespace(
                    path="result.txt",
                    content=b"written",
                    mime_type="text/plain",
                ),
            ),
        )
        mock_vfs = SimpleNamespace(
            read_bytes=AsyncMock(return_value=(b"written", "text/plain")),
        )

        tokens = python_service.push_runtime_context(mock_vfs, "/project")
        try:
            result = asyncio.run(
                python_service.execute_python_request(
                    "",
                    python_service.PythonExecutionRequest(
                        mode="script",
                        entrypoint="script.py",
                        workspace_files=(
                            python_service.PythonWorkspaceFile(
                                path="script.py",
                                content=b"print('done')",
                                mime_type="text/x-python",
                            ),
                        ),
                    ),
                )
            )
        finally:
            python_service.pop_runtime_context(tokens)

        mocked_execute.assert_awaited_once()
        mock_vfs.read_bytes.assert_not_awaited()
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "done\n")
        self.assertEqual(len(result.output_files), 1)
        self.assertEqual(result.output_files[0].path, "result.txt")
        self.assertEqual(result.output_files[0].content, b"written")
