from __future__ import annotations

import asyncio
import builtins
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any, cast
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from nova.exec_runner.docker_backend import (
    CACHE_ROOT_IN_CONTAINER,
    SESSION_ROOT_IN_CONTAINER,
    SITECUSTOMIZE_PATH,
    WORKSPACE_ROOT_IN_CONTAINER,
    DockerExecRunnerBackend,
    ExecRunnerConfig,
    ExecSession,
    load_exec_runner_config_from_env,
)
from nova.exec_runner import service as exec_runner_service
from nova.exec_runner.shared import (
    ExecSessionSelector,
    PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE,
    RUNNER_INTERNAL_DIRNAME,
    SandboxShellResult,
    rewrite_output_paths_from_workspace,
    rewrite_shell_command_for_workspace,
)


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
    @patch("nova.exec_runner.service.httpx.AsyncClient.request", new_callable=AsyncMock)
    def test_test_exec_runner_access_calls_remote_healthcheck(self, mocked_request):
        mocked_request.return_value = SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

        result = asyncio.run(exec_runner_service.test_exec_runner_access())

        self.assertEqual(result["status"], "success")
        self.assertEqual(mocked_request.await_args.args[0], "GET")
        self.assertIn("/healthz", mocked_request.await_args.args[1])
        self.assertEqual(
            mocked_request.await_args.kwargs["headers"]["Authorization"],
            "Bearer runner-token",
        )

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service._apply_diff_bundle", new_callable=AsyncMock)
    @patch("nova.exec_runner.service._parse_multipart_response")
    @patch("nova.exec_runner.service.httpx.AsyncClient.request", new_callable=AsyncMock)
    @patch("nova.exec_runner.service._build_sync_bundle", new_callable=AsyncMock)
    def test_execute_sandbox_shell_command_posts_to_runner_and_updates_cwd(
        self,
        mocked_build_bundle,
        mocked_request,
        mocked_parse_response,
        mocked_apply_diff,
    ):
        mocked_build_bundle.return_value = b"sync-bundle"
        mocked_request.return_value = SimpleNamespace(status_code=200)
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
                vfs=cast(Any, mock_vfs),
                command="pwd",
            )
        )

        self.assertEqual(result, SandboxShellResult(stdout="ok\n", stderr="", status=0, cwd_after="/workspace"))
        self.assertEqual(sync_meta["synced_paths"], ["/workspace/new.txt"])
        self.assertEqual(mock_vfs.session_state["cwd"], "/workspace")
        self.assertEqual(mocked_request.await_args.args[0], "POST")
        self.assertIn("/v1/sessions/exec", mocked_request.await_args.args[1])
        metadata = json.loads(mocked_request.await_args.kwargs["data"]["metadata"])
        self.assertEqual(metadata["cwd"], "/")
        self.assertEqual(metadata["command"], "pwd")
        self.assertEqual(metadata["selector"]["thread_id"], 2)

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service.httpx.AsyncClient.request", new_callable=AsyncMock)
    def test_delete_sandbox_session_calls_runner_delete_endpoint(self, mocked_request):
        mocked_request.return_value = SimpleNamespace(status_code=200)
        mock_vfs = SimpleNamespace(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
            agent_config=SimpleNamespace(id=3),
        )

        asyncio.run(exec_runner_service.delete_sandbox_session(cast(Any, mock_vfs)))

        self.assertEqual(mocked_request.await_args.args[0], "DELETE")
        self.assertIn("/v1/sessions/user-1--thread-2--agent-3", mocked_request.await_args.args[1])

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch("nova.exec_runner.service.httpx.AsyncClient.request", new_callable=AsyncMock)
    def test_delete_thread_sandbox_sessions_calls_runner_thread_endpoint(self, mocked_request):
        mocked_request.return_value = SimpleNamespace(status_code=200)

        asyncio.run(exec_runner_service.delete_thread_sandbox_sessions(user_id=7, thread_id=9))

        self.assertEqual(mocked_request.await_args.args[0], "DELETE")
        self.assertIn("/v1/users/7/threads/9/sessions", mocked_request.await_args.args[1])


class DockerExecRunnerBackendTests(SimpleTestCase):
    def _build_backend(
        self,
        *,
        sandbox_no_new_privileges: bool = True,
        state_root: Path | None = None,
        session_ttl_seconds: int = 14400,
    ) -> DockerExecRunnerBackend:
        return DockerExecRunnerBackend(
            ExecRunnerConfig(
                shared_token="runner-token",
                state_root=state_root or Path("/tmp/nova-exec-runner-tests"),
                session_ttl_seconds=session_ttl_seconds,
                gc_interval_seconds=900,
                sandbox_image="amairesse/nova:latest",
                sandbox_network="nova_exec-sandbox-net",
                sandbox_memory_limit_mb=1024,
                sandbox_cpu_limit="1.0",
                sandbox_pids_limit=256,
                sandbox_no_new_privileges=sandbox_no_new_privileges,
                max_sync_bytes=50 * 1024 * 1024,
                max_diff_bytes=50 * 1024 * 1024,
                proxy_url="http://exec-runner:8091",
                cache_max_bytes=5 * 1024 * 1024 * 1024,
                cache_target_bytes=3 * 1024 * 1024 * 1024,
                cache_max_age_days=14,
            )
        )

    def _build_session(self) -> ExecSession:
        return ExecSession(
            selector=ExecSessionSelector(user_id=1, thread_id=2, agent_id=3),
            container_name="nova-exec-test",
            volume_name="nova-exec-session-test",
            metadata_dir=Path("/tmp/nova-exec-runner-tests/sessions/test"),
            metadata_path=Path("/tmp/nova-exec-runner-tests/sessions/test/session.json"),
        )

    def test_load_exec_runner_config_enables_no_new_privileges_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_exec_runner_config_from_env()

        self.assertTrue(config.sandbox_no_new_privileges)
        self.assertEqual(config.session_ttl_seconds, 14400)
        self.assertEqual(config.gc_interval_seconds, 900)
        self.assertEqual(config.cache_max_bytes, 5 * 1024 * 1024 * 1024)
        self.assertEqual(config.cache_target_bytes, 3 * 1024 * 1024 * 1024)
        self.assertEqual(config.cache_max_age_days, 14)

    def test_load_exec_runner_config_accepts_falsey_no_new_privileges_values(self):
        with patch.dict(
            os.environ,
            {"EXEC_RUNNER_SANDBOX_NO_NEW_PRIVILEGES": "false"},
            clear=True,
        ):
            config = load_exec_runner_config_from_env()

        self.assertFalse(config.sandbox_no_new_privileges)

    def test_write_bytes_into_container_streams_content_over_stdin(self):
        backend = self._build_backend()
        backend._run_process = AsyncMock(return_value=("", "", 0))

        asyncio.run(
            backend._write_bytes_into_container(
                "nova-exec-test",
                Path("/tmp/example.txt"),
                b"hello world",
            )
        )

        await_args = backend._run_process.await_args
        assert await_args is not None
        command = await_args.args[0]
        self.assertEqual(command[:6], ["docker", "exec", "-i", "-u", "nova", "nova-exec-test"])
        self.assertEqual(command[-1], "/tmp/example.txt")
        self.assertEqual(
            await_args.kwargs["input_bytes"],
            b"hello world",
        )

    def test_write_text_into_container_delegates_to_binary_writer(self):
        backend = self._build_backend()
        backend._write_bytes_into_container = AsyncMock()

        asyncio.run(
            backend._write_text_into_container(
                "nova-exec-test",
                Path("/srv/nova-session/workspace/.nova_runner/command.sh"),
                "echo test",
            )
        )

        backend._write_bytes_into_container.assert_awaited_once_with(
            "nova-exec-test",
            Path("/srv/nova-session/workspace/.nova_runner/command.sh"),
            b"echo test",
        )

    def test_sync_bundle_uses_container_writer_instead_of_docker_cp(self):
        backend = self._build_backend()
        backend._write_bytes_into_container = AsyncMock()
        backend._docker_exec = AsyncMock()
        session = self._build_session()

        asyncio.run(backend._sync_bundle_into_session(session, b"sync-bundle"))

        backend._write_bytes_into_container.assert_awaited_once_with(
            "nova-exec-test",
            Path("/tmp/nova-sync.tar.gz"),
            b"sync-bundle",
        )
        backend._docker_exec.assert_awaited_once()

    def test_sync_bundle_unlocks_and_replaces_projected_roots_before_extracting(self):
        backend = self._build_backend()
        backend._write_bytes_into_container = AsyncMock()
        backend._docker_exec = AsyncMock()
        session = self._build_session()

        asyncio.run(backend._sync_bundle_into_session(session, b"sync-bundle"))

        await_args = backend._docker_exec.await_args
        assert await_args is not None
        script = str(await_args.args[1])
        self.assertIn(f'for root in "{WORKSPACE_ROOT_IN_CONTAINER / "skills"}"', script)
        self.assertIn(f'"{WORKSPACE_ROOT_IN_CONTAINER / "inbox"}"', script)
        self.assertIn(f'"{WORKSPACE_ROOT_IN_CONTAINER / "history"}"', script)
        self.assertIn('chmod -R u+w "$root" || true', script)
        self.assertIn('rm -rf "$root"', script)
        self.assertIn(f'! -name "{RUNNER_INTERNAL_DIRNAME}"', script)
        self.assertIn('! -name "skills" ! -name "inbox" ! -name "history"', script)

    def test_create_container_includes_no_new_privileges_when_enabled(self):
        backend = self._build_backend(sandbox_no_new_privileges=True)
        backend._run_docker = AsyncMock(return_value="container-id")

        asyncio.run(backend._create_container(self._build_session()))

        await_args = backend._run_docker.await_args
        assert await_args is not None
        docker_args = list(await_args.args)
        self.assertIn("--security-opt", docker_args)
        self.assertIn("no-new-privileges", docker_args)
        self.assertIn(f"source=nova-exec-session-test,target={SESSION_ROOT_IN_CONTAINER}", docker_args)
        self.assertIn(f"source=nova-exec-cache-user-1,target={CACHE_ROOT_IN_CONTAINER}", docker_args)
        self.assertIn(
            (
                f'mkdir -p "{WORKSPACE_ROOT_IN_CONTAINER}" '
                f'"{SESSION_ROOT_IN_CONTAINER / "home"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "pip"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "uv"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "npm"}" '
                "&& exec sleep infinity"
            ),
            docker_args,
        )

    def test_create_container_omits_no_new_privileges_when_disabled(self):
        backend = self._build_backend(sandbox_no_new_privileges=False)
        backend._run_docker = AsyncMock(return_value="container-id")

        asyncio.run(backend._create_container(self._build_session()))

        await_args = backend._run_docker.await_args
        assert await_args is not None
        docker_args = list(await_args.args)
        self.assertNotIn("--security-opt", docker_args)
        self.assertNotIn("no-new-privileges", docker_args)

    def test_create_container_sets_exec_runner_session_labels(self):
        backend = self._build_backend()
        backend._run_docker = AsyncMock(return_value="container-id")

        asyncio.run(backend._create_container(self._build_session()))

        await_args = backend._run_docker.await_args
        assert await_args is not None
        docker_args = list(await_args.args)
        self.assertIn("--label", docker_args)
        rendered = " ".join(str(item) for item in docker_args)
        self.assertIn("nova.exec_runner.managed=true", rendered)
        self.assertIn("nova.exec_runner.resource=session", rendered)
        self.assertIn("nova.exec_runner.session_id=user-1--thread-2--agent-3", rendered)

    def test_ensure_session_recreates_container_when_cache_mount_uses_legacy_volume(self):
        backend = self._build_backend()
        backend._ensure_volume = AsyncMock()
        backend._ensure_user_cache_volume = AsyncMock()
        backend._container_exists = AsyncMock(return_value=True)
        backend._container_has_mount = AsyncMock(return_value=False)
        backend._remove_container = AsyncMock(return_value=True)
        backend._create_container = AsyncMock()
        backend._ensure_container_running = AsyncMock()

        session = asyncio.run(backend._ensure_session(ExecSessionSelector(user_id=1, thread_id=2, agent_id=3)))

        self.assertEqual(session.container_name, "nova-exec-user-1--thread-2--agent-3")
        backend._remove_container.assert_awaited_once_with("nova-exec-user-1--thread-2--agent-3")
        backend._create_container.assert_awaited_once()
        backend._ensure_container_running.assert_not_awaited()

    def test_delete_sessions_for_thread_removes_matching_resources(self):
        with tempfile.TemporaryDirectory() as state_dir:
            backend = self._build_backend(
                state_root=Path(state_dir),
            )
            target_dir = Path(state_dir) / "sessions" / "user-7--thread-9--agent-3"
            target_dir.mkdir(parents=True)
            (target_dir / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": "user-7--thread-9--agent-3",
                        "user_id": "7",
                        "thread_id": "9",
                        "agent_id": "3",
                        "last_used_at": "2026-04-15T10:00:00+00:00",
                        "container_name": "nova-exec-user-7--thread-9--agent-3",
                        "volume_name": "nova-exec-session-user-7--thread-9--agent-3",
                    }
                ),
                encoding="utf-8",
            )
            other_dir = Path(state_dir) / "sessions" / "user-7--thread-10--agent-4"
            other_dir.mkdir(parents=True)
            (other_dir / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": "user-7--thread-10--agent-4",
                        "user_id": "7",
                        "thread_id": "10",
                        "agent_id": "4",
                        "last_used_at": "2026-04-15T10:00:00+00:00",
                        "container_name": "nova-exec-user-7--thread-10--agent-4",
                        "volume_name": "nova-exec-session-user-7--thread-10--agent-4",
                    }
                ),
                encoding="utf-8",
            )
            backend._initialized = True
            backend._remove_container = AsyncMock()
            backend._remove_volume = AsyncMock()
            backend._list_managed_resources = AsyncMock(return_value=([], []))

            removed = asyncio.run(backend.delete_sessions_for_thread(user_id=7, thread_id=9))

            self.assertEqual(removed, 1)
            backend._remove_container.assert_awaited_once_with("nova-exec-user-7--thread-9--agent-3")
            backend._remove_volume.assert_awaited_once_with("nova-exec-session-user-7--thread-9--agent-3")
            self.assertFalse(target_dir.exists())
            self.assertTrue(other_dir.exists())

    def test_prune_cache_volumes_aggregates_all_user_cache_volumes(self):
        backend = self._build_backend()
        backend._list_managed_cache_volumes = AsyncMock(
            return_value=[
                {"name": "nova-exec-cache-user-1", "labels": {}},
                {"name": "nova-exec-cache-user-2", "labels": {}},
            ]
        )
        backend._prune_cache_volume = AsyncMock(
            side_effect=[
                {"files_removed": 2, "directories_removed": 1, "bytes_reclaimed": 12, "errors": 0},
                {"files_removed": 1, "directories_removed": 0, "bytes_reclaimed": 7, "errors": 0},
            ]
        )

        summary = asyncio.run(backend._prune_cache_volumes_locked())

        self.assertEqual(summary["files_removed"], 3)
        self.assertEqual(summary["directories_removed"], 1)
        self.assertEqual(summary["bytes_reclaimed"], 19)
        self.assertEqual(summary["errors"], 0)

    def test_cache_volume_name_is_scoped_per_user(self):
        backend = self._build_backend()

        self.assertEqual(backend._cache_volume_name(7), "nova-exec-cache-user-7")
        self.assertEqual(backend._cache_volume_name("user@example.com"), "nova-exec-cache-user-user-example-com")

    def test_ensure_user_cache_volume_creates_labeled_volume(self):
        backend = self._build_backend()
        backend._ensure_volume = AsyncMock()

        asyncio.run(backend._ensure_user_cache_volume("user@example.com"))

        backend._ensure_volume.assert_awaited_once_with(
            "nova-exec-cache-user-user-example-com",
            CACHE_ROOT_IN_CONTAINER,
            labels={
                "nova.exec_runner.managed": "true",
                "nova.exec_runner.resource": "cache",
                "nova.exec_runner.user_id": "user@example.com",
            },
        )

    def test_ensure_volume_initializes_new_volume_once_without_recursive_permissions(self):
        backend = self._build_backend()
        backend._volume_exists = AsyncMock(return_value=False)
        backend._run_docker = AsyncMock(return_value="")

        created = asyncio.run(
            backend._ensure_volume(
                "nova-exec-cache-user-1",
                CACHE_ROOT_IN_CONTAINER,
                labels={"nova.exec_runner.managed": "true"},
            )
        )

        self.assertTrue(created)
        self.assertEqual(backend._run_docker.await_count, 2)
        create_call = backend._run_docker.await_args_list[0]
        helper_call = backend._run_docker.await_args_list[1]
        self.assertEqual(create_call.args[:2], ("volume", "create"))
        helper_script = str(helper_call.args[-1])
        self.assertIn(f'mkdir -p "{CACHE_ROOT_IN_CONTAINER}"', helper_script)
        self.assertIn(f'chown nova:nova "{CACHE_ROOT_IN_CONTAINER}"', helper_script)
        self.assertIn(f'chmod u+rwx,go-rwx "{CACHE_ROOT_IN_CONTAINER}"', helper_script)
        self.assertNotIn("chown -R", helper_script)
        self.assertNotIn("chmod -R", helper_script)

    def test_ensure_volume_skips_permission_init_for_existing_volume(self):
        backend = self._build_backend()
        backend._volume_exists = AsyncMock(return_value=True)
        backend._run_docker = AsyncMock(return_value="")

        created = asyncio.run(
            backend._ensure_volume(
                "nova-exec-cache-user-1",
                CACHE_ROOT_IN_CONTAINER,
                labels={"nova.exec_runner.managed": "true"},
            )
        )

        self.assertFalse(created)
        backend._run_docker.assert_not_awaited()

    def test_cleanup_processes_excludes_its_own_shell(self):
        backend = self._build_backend()
        backend._docker_exec = AsyncMock(return_value=None)

        asyncio.run(backend._cleanup_processes("nova-exec-test"))

        await_args = backend._docker_exec.await_args
        assert await_args is not None
        self.assertEqual(await_args.args[0], "nova-exec-test")
        cleanup_script = await_args.args[1]
        self.assertIn("self_pid=$$", cleanup_script)
        self.assertIn("$1 != 1 && $1 != self", cleanup_script)
        self.assertTrue(cleanup_script.endswith("exit 0"))


class ExecRunnerSharedTests(SimpleTestCase):
    def test_rewrite_shell_command_preserves_dev_null_redirection(self):
        rewritten = rewrite_shell_command_for_workspace(
            'find / -name "*.csv" 2>/dev/null || echo "No CSV files found"',
            WORKSPACE_ROOT_IN_CONTAINER,
        )

        self.assertIn(f'find {WORKSPACE_ROOT_IN_CONTAINER}', rewritten)
        self.assertIn('2> /dev/null', rewritten)
        self.assertIn('|| echo', rewritten)

    def test_rewrite_shell_command_leaves_special_system_paths_intact(self):
        rewritten = rewrite_shell_command_for_workspace(
            'python -c "print(1)" > /dev/null; cat /proc/version; ls /sys',
            WORKSPACE_ROOT_IN_CONTAINER,
        )

        self.assertIn('> /dev/null', rewritten)
        self.assertIn('cat /proc/version', rewritten)
        self.assertIn('ls /sys', rewritten)

    def test_rewrite_output_paths_keeps_single_leading_slash_for_root_files(self):
        rendered = rewrite_output_paths_from_workspace(
            f"{WORKSPACE_ROOT_IN_CONTAINER}/openrouter_activity_2026-04-15.csv\n",
            WORKSPACE_ROOT_IN_CONTAINER,
        )

        self.assertEqual(rendered, "/openrouter_activity_2026-04-15.csv\n")

    def test_python_workspace_sitecustomize_maps_absolute_nova_paths(self):
        originals = {
            "open": builtins.open,
            "io_open": io.open,
            "listdir": os.listdir,
            "access": os.access,
            "stat": os.stat,
        }
        previous_root = os.environ.get("NOVA_WORKSPACE_ROOT")
        restore = None
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace = Path(workspace_dir)
            (workspace / "openrouter_activity_2026-04-15.csv").write_text(
                "col\nvalue\n",
                encoding="utf-8",
            )
            os.environ["NOVA_WORKSPACE_ROOT"] = workspace_dir
            namespace: dict[str, Any] = {}
            try:
                exec(PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE, namespace, namespace)
                restore = namespace.get("_restore_nova_workspace_shims")
                self.assertIn(
                    "openrouter_activity_2026-04-15.csv",
                    os.listdir("/"),
                )
                with open("/openrouter_activity_2026-04-15.csv", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "col\nvalue\n")
                self.assertTrue(os.access("/openrouter_activity_2026-04-15.csv", os.R_OK))
            finally:
                if callable(restore):
                    restore()
                if previous_root is None:
                    os.environ.pop("NOVA_WORKSPACE_ROOT", None)
                else:
                    os.environ["NOVA_WORKSPACE_ROOT"] = previous_root
                self.assertIs(builtins.open, originals["open"])
                self.assertIs(io.open, originals["io_open"])
                self.assertIs(os.listdir, originals["listdir"])
                self.assertIs(os.access, originals["access"])
                self.assertIs(os.stat, originals["stat"])

    def test_python_workspace_sitecustomize_preserves_python_environment_paths(self):
        previous_root = os.environ.get("NOVA_WORKSPACE_ROOT")
        previous_home = os.environ.get("HOME")
        restore = None
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as home_dir:
            workspace = Path(workspace_dir)
            home = Path(home_dir)
            (workspace / "data.csv").write_text("workspace\n", encoding="utf-8")
            config_path = home / "matplotlibrc"
            config_path.write_text("backend: Agg\n", encoding="utf-8")
            os.environ["NOVA_WORKSPACE_ROOT"] = workspace_dir
            os.environ["HOME"] = home_dir
            namespace: dict[str, Any] = {}
            try:
                exec(PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE, namespace, namespace)
                restore = namespace.get("_restore_nova_workspace_shims")
                with open("/data.csv", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "workspace\n")
                with open(str(config_path), encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "backend: Agg\n")
                self.assertTrue(os.access(str(config_path), os.R_OK))
            finally:
                if callable(restore):
                    restore()
                if previous_root is None:
                    os.environ.pop("NOVA_WORKSPACE_ROOT", None)
                else:
                    os.environ["NOVA_WORKSPACE_ROOT"] = previous_root
                if previous_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = previous_home

    def test_run_session_command_installs_python_workspace_sitecustomize(self):
        backend = DockerExecRunnerBackend(
            ExecRunnerConfig(
                shared_token="runner-token",
                state_root=Path("/tmp/nova-exec-runner-tests"),
                session_ttl_seconds=14400,
                gc_interval_seconds=900,
                sandbox_image="amairesse/nova:latest",
                sandbox_network="nova_exec-sandbox-net",
                sandbox_memory_limit_mb=1024,
                sandbox_cpu_limit="1.0",
                sandbox_pids_limit=256,
                sandbox_no_new_privileges=True,
                max_sync_bytes=50 * 1024 * 1024,
                max_diff_bytes=50 * 1024 * 1024,
                proxy_url="http://exec-runner:8091",
                cache_max_bytes=5 * 1024 * 1024 * 1024,
                cache_target_bytes=3 * 1024 * 1024 * 1024,
                cache_max_age_days=14,
            )
        )
        backend._load_persisted_env = AsyncMock(return_value={})
        backend._write_text_into_container = AsyncMock()
        backend._docker_exec_capture = AsyncMock(
            return_value=("", "", 0)
        )
        backend._cleanup_processes = AsyncMock()
        backend._read_text_from_container = AsyncMock(return_value=str(WORKSPACE_ROOT_IN_CONTAINER))

        asyncio.run(
            backend._run_session_command(
                ExecSession(
                    selector=ExecSessionSelector(user_id=1, thread_id=2, agent_id=3),
                    container_name="nova-exec-test",
                    volume_name="nova-exec-session-test",
                    metadata_dir=Path("/tmp/nova-exec-runner-tests/sessions/test"),
                    metadata_path=Path("/tmp/nova-exec-runner-tests/sessions/test/session.json"),
                ),
                command='python -c "print(1)"',
                cwd="/",
                ensure_python=True,
            )
        )

        await_calls = backend._write_text_into_container.await_args_list
        self.assertGreaterEqual(len(await_calls), 2)
        self.assertEqual(await_calls[0].args[1], SITECUSTOMIZE_PATH)
        self.assertIn("_nova_install_python_workspace_shims", await_calls[0].args[2])
        command_script = await_calls[1].args[2]
        self.assertIn('export NOVA_WORKSPACE_ROOT="/srv/nova-session/workspace"', command_script)
        self.assertIn('export PYTHONPATH="/srv/nova-session/workspace/.nova_runner"', command_script)
        self.assertIn("unset NOVA_WORKSPACE_ROOT", command_script)
        self.assertIn("unset PYTHONPATH", command_script)

    def test_run_session_command_does_not_install_python_workspace_sitecustomize_for_non_python_commands(self):
        backend = DockerExecRunnerBackend(
            ExecRunnerConfig(
                shared_token="runner-token",
                state_root=Path("/tmp/nova-exec-runner-tests"),
                session_ttl_seconds=14400,
                gc_interval_seconds=900,
                sandbox_image="amairesse/nova:latest",
                sandbox_network="nova_exec-sandbox-net",
                sandbox_memory_limit_mb=1024,
                sandbox_cpu_limit="1.0",
                sandbox_pids_limit=256,
                sandbox_no_new_privileges=True,
                max_sync_bytes=50 * 1024 * 1024,
                max_diff_bytes=50 * 1024 * 1024,
                proxy_url="http://exec-runner:8091",
                cache_max_bytes=5 * 1024 * 1024 * 1024,
                cache_target_bytes=3 * 1024 * 1024 * 1024,
                cache_max_age_days=14,
            )
        )
        backend._load_persisted_env = AsyncMock(return_value={})
        backend._write_text_into_container = AsyncMock()
        backend._docker_exec_capture = AsyncMock(
            return_value=("", "", 0)
        )
        backend._cleanup_processes = AsyncMock()
        backend._read_text_from_container = AsyncMock(return_value=str(WORKSPACE_ROOT_IN_CONTAINER))

        asyncio.run(
            backend._run_session_command(
                ExecSession(
                    selector=ExecSessionSelector(user_id=1, thread_id=2, agent_id=3),
                    container_name="nova-exec-test",
                    volume_name="nova-exec-session-test",
                    metadata_dir=Path("/tmp/nova-exec-runner-tests/sessions/test"),
                    metadata_path=Path("/tmp/nova-exec-runner-tests/sessions/test/session.json"),
                ),
                command="pip install --user pandas",
                cwd="/",
                ensure_python=False,
            )
        )

        await_calls = backend._write_text_into_container.await_args_list
        self.assertEqual(len(await_calls), 1)
        self.assertEqual(await_calls[0].args[1].name, "command.sh")
        command_script = await_calls[0].args[2]
        self.assertNotIn("NOVA_WORKSPACE_ROOT", command_script)
        self.assertNotIn("sitecustomize.py", command_script)
