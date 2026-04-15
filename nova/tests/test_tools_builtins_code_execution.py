from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from nova.exec_runner.service import SandboxShellResult
from nova.plugins.python import service as python_service


class PythonBuiltinsTests(SimpleTestCase):
    @override_settings(EXEC_RUNNER_ENABLED=True)
    def test_test_exec_runner_access_reports_success_when_enabled(self):
        result = asyncio.run(python_service.test_exec_runner_access())

        self.assertEqual(result["status"], "success")
        self.assertIn("sandbox terminal", result["message"])

    @override_settings(EXEC_RUNNER_ENABLED=False)
    def test_test_exec_runner_access_reports_error_when_disabled(self):
        result = asyncio.run(python_service.test_exec_runner_access())

        self.assertEqual(result["status"], "error")
        self.assertIn("disabled", result["message"])

    def test_execute_python_request_local_fallback_runs_inline_code(self):
        result = asyncio.run(
            python_service.execute_python_request(
                "",
                python_service.PythonExecutionRequest(
                    code="print('hello from python')",
                    mode="inline",
                ),
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "hello from python")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.output_files, ())

    def test_execute_python_request_local_fallback_returns_generated_files(self):
        result = asyncio.run(
            python_service.execute_python_request(
                "",
                python_service.PythonExecutionRequest(
                    mode="script",
                    entrypoint="script.py",
                    workspace_files=(
                        python_service.PythonWorkspaceFile(
                            path="script.py",
                            content=(
                                b"from pathlib import Path\n"
                                b"Path('result.txt').write_text('written from script', encoding='utf-8')\n"
                                b"print('done')\n"
                            ),
                            mime_type="text/x-python",
                        ),
                    ),
                ),
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "done")
        self.assertEqual(len(result.output_files), 1)
        self.assertEqual(result.output_files[0].path, "result.txt")
        self.assertEqual(result.output_files[0].content, b"written from script")
        self.assertEqual(result.output_files[0].mime_type, "text/plain")

    @patch("nova.plugins.python.service.exec_runner_service.execute_sandbox_shell_command", new_callable=AsyncMock)
    def test_execute_python_request_uses_runtime_context_and_collects_synced_files(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(
                stdout="done\n",
                stderr="",
                status=0,
                cwd_after="/project",
            ),
            {"synced_paths": ["/project/result.txt"]},
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
        mock_vfs.read_bytes.assert_awaited_once_with("/project/result.txt")
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "done\n")
        self.assertEqual(len(result.output_files), 1)
        self.assertEqual(result.output_files[0].path, "result.txt")
        self.assertEqual(result.output_files[0].content, b"written")
