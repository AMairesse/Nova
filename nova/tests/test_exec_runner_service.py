from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from nova.exec_runner import service as exec_runner_service
from nova.exec_runner.shared import SandboxShellResult


class ExecRunnerServiceTests(SimpleTestCase):
    @override_settings(EXEC_RUNNER_ENABLED=False)
    def test_test_exec_runner_access_reports_disabled_when_runner_is_off(self):
        result = asyncio.run(exec_runner_service.test_exec_runner_access())

        self.assertEqual(result["status"], "error")
        self.assertIn("disabled", result["message"])

    @override_settings(EXEC_RUNNER_ENABLED=True, EXEC_RUNNER_BASE_URL="", EXEC_RUNNER_SHARED_TOKEN="")
    def test_test_exec_runner_access_reports_not_configured_when_values_are_missing(self):
        result = asyncio.run(exec_runner_service.test_exec_runner_access())

        self.assertEqual(result["status"], "error")
        self.assertIn("not configured", result["message"])

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service.httpx.AsyncClient.get", new_callable=AsyncMock)
    def test_test_exec_runner_access_calls_remote_healthcheck(self, mocked_get):
        mocked_get.return_value = SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

        result = asyncio.run(exec_runner_service.test_exec_runner_access())

        self.assertEqual(result["status"], "success")
        self.assertIn("/healthz", mocked_get.await_args.args[0])
        self.assertEqual(
            mocked_get.await_args.kwargs["headers"]["Authorization"],
            "Bearer runner-token",
        )

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service._apply_diff_bundle", new_callable=AsyncMock)
    @patch("nova.exec_runner.service._parse_multipart_response")
    @patch("nova.exec_runner.service.httpx.AsyncClient.post", new_callable=AsyncMock)
    @patch("nova.exec_runner.service._build_sync_bundle", new_callable=AsyncMock)
    def test_execute_sandbox_shell_command_posts_to_runner_and_updates_cwd(
        self,
        mocked_build_bundle,
        mocked_post,
        mocked_parse_response,
        mocked_apply_diff,
    ):
        mocked_build_bundle.return_value = b"sync-bundle"
        mocked_post.return_value = SimpleNamespace(status_code=200)
        mocked_parse_response.return_value = (
            {
                "stdout": "ok\n",
                "stderr": "",
                "status": 0,
                "cwd_after": "/workspace",
                "execution_plane": "sandbox",
                "removed_paths": ["/tmp/old.txt"],
                "directory_paths": ["/tmp", "/workspace"],
            },
            b"diff-bundle",
        )
        mocked_apply_diff.return_value = {
            "synced_paths": ["/workspace/new.txt"],
            "removed_paths": ["/tmp/old.txt"],
        }
        mock_vfs = SimpleNamespace(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
            agent_config=SimpleNamespace(id=3),
            session_state={"cwd": "/inbox"},
            set_cwd=AsyncMock(),
        )
        mock_vfs.set_cwd = lambda cwd: mock_vfs.session_state.__setitem__("cwd", cwd)

        result, sync_meta = asyncio.run(
            exec_runner_service.execute_sandbox_shell_command(
                vfs=mock_vfs,
                command="pwd",
            )
        )

        self.assertEqual(result, SandboxShellResult(stdout="ok\n", stderr="", status=0, cwd_after="/workspace"))
        self.assertEqual(sync_meta["synced_paths"], ["/workspace/new.txt"])
        self.assertEqual(mock_vfs.session_state["cwd"], "/workspace")
        metadata = json.loads(mocked_post.await_args.kwargs["data"]["metadata"])
        self.assertEqual(metadata["cwd"], "/")
        self.assertEqual(metadata["command"], "pwd")
        self.assertEqual(metadata["selector"]["thread_id"], 2)

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service.httpx.AsyncClient.delete", new_callable=AsyncMock)
    def test_delete_sandbox_session_calls_runner_delete_endpoint(self, mocked_delete):
        mocked_delete.return_value = SimpleNamespace(status_code=200)
        mock_vfs = SimpleNamespace(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
            agent_config=SimpleNamespace(id=3),
        )

        asyncio.run(exec_runner_service.delete_sandbox_session(mock_vfs))

        self.assertIn("/v1/sessions/user-1--thread-2--agent-3", mocked_delete.await_args.args[0])
