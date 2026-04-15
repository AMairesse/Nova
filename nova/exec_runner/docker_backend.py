from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nova.exec_runner.shared import (
    ExecRunnerError,
    ExecSessionSelector,
    RUNNER_COMMAND_FILENAME,
    RUNNER_CWD_FILENAME,
    RUNNER_ENV_FILENAME,
    RUNNER_INTERNAL_DIRNAME,
    SandboxShellResult,
    encode_environment_script,
    normalize_sandbox_path,
    rewrite_output_paths_from_workspace,
    rewrite_shell_command_for_workspace,
    vfs_path_for_workspace_path,
)


WORKSPACE_ROOT_IN_CONTAINER = Path("/srv/nova-session/workspace")
SESSION_ROOT_IN_CONTAINER = Path("/srv/nova-session")
CACHE_ROOT_IN_CONTAINER = Path("/srv/nova-cache")
BEFORE_MANIFEST_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / "before.json"
DIFF_BUNDLE_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / "diff.tar.gz"
DIFF_METADATA_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / "diff.json"
COMMAND_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / RUNNER_COMMAND_FILENAME
CWD_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / RUNNER_CWD_FILENAME
ENV_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / RUNNER_ENV_FILENAME


@dataclass(slots=True, frozen=True)
class ExecRunnerConfig:
    shared_token: str
    state_root: Path
    session_ttl_seconds: int
    sandbox_image: str
    sandbox_network: str
    sandbox_memory_limit_mb: int
    sandbox_cpu_limit: str
    sandbox_pids_limit: int
    max_sync_bytes: int
    max_diff_bytes: int
    proxy_url: str
    shared_cache_volume: str = "exec_runner_cache"
    command_timeout_seconds: int = 300


@dataclass(slots=True, frozen=True)
class ExecSession:
    selector: ExecSessionSelector
    container_name: str
    volume_name: str
    metadata_dir: Path
    metadata_path: Path


@dataclass(slots=True, frozen=True)
class ExecResponse:
    result: SandboxShellResult
    removed_paths: tuple[str, ...]
    directory_paths: tuple[str, ...]
    diff_bundle_bytes: bytes


def load_exec_runner_config_from_env() -> ExecRunnerConfig:
    shared_token = str(os.getenv("EXEC_RUNNER_SHARED_TOKEN", "") or "").strip()
    state_root = Path(str(os.getenv("EXEC_RUNNER_STATE_ROOT", "/var/lib/nova-exec-runner")).strip())
    session_ttl_seconds = max(int(os.getenv("EXEC_RUNNER_SESSION_TTL_SECONDS", "3600")), 60)
    sandbox_image = str(os.getenv("EXEC_RUNNER_SANDBOX_IMAGE", "amairesse/nova:latest")).strip()
    sandbox_network = str(os.getenv("EXEC_RUNNER_SANDBOX_NETWORK", "exec-sandbox-net")).strip()
    sandbox_memory_limit_mb = max(int(os.getenv("EXEC_RUNNER_SANDBOX_MEMORY_LIMIT_MB", "1024")), 256)
    sandbox_cpu_limit = str(os.getenv("EXEC_RUNNER_SANDBOX_CPU_LIMIT", "1.0")).strip() or "1.0"
    sandbox_pids_limit = max(int(os.getenv("EXEC_RUNNER_SANDBOX_PIDS_LIMIT", "256")), 64)
    max_sync_bytes = max(int(os.getenv("EXEC_RUNNER_MAX_SYNC_BYTES", str(50 * 1024 * 1024))), 1024 * 1024)
    max_diff_bytes = max(int(os.getenv("EXEC_RUNNER_MAX_DIFF_BYTES", str(50 * 1024 * 1024))), 1024 * 1024)
    proxy_port = max(int(os.getenv("EXEC_RUNNER_PROXY_PORT", "8091")), 1)
    proxy_url = str(os.getenv("EXEC_RUNNER_PROXY_URL", f"http://exec-runner:{proxy_port}")).strip()
    command_timeout_seconds = max(int(os.getenv("EXEC_RUNNER_COMMAND_TIMEOUT_SECONDS", "300")), 5)
    return ExecRunnerConfig(
        shared_token=shared_token,
        state_root=state_root,
        session_ttl_seconds=session_ttl_seconds,
        sandbox_image=sandbox_image,
        sandbox_network=sandbox_network,
        sandbox_memory_limit_mb=sandbox_memory_limit_mb,
        sandbox_cpu_limit=sandbox_cpu_limit,
        sandbox_pids_limit=sandbox_pids_limit,
        max_sync_bytes=max_sync_bytes,
        max_diff_bytes=max_diff_bytes,
        proxy_url=proxy_url,
        command_timeout_seconds=command_timeout_seconds,
    )


class DockerExecRunnerBackend:
    def __init__(self, config: ExecRunnerConfig):
        self.config = config
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self.config.state_root.mkdir, parents=True, exist_ok=True)
        await self._run_docker("version", "--format", "{{json .Server.Version}}")
        await self._ensure_cache_volume()
        await self._gc_expired_sessions()
        self._initialized = True

    async def healthcheck(self) -> dict[str, str]:
        await self.initialize()
        return {
            "status": "ok",
            "sandbox_image": self.config.sandbox_image,
            "sandbox_network": self.config.sandbox_network,
        }

    async def delete_session(self, selector: ExecSessionSelector) -> None:
        session = self._session(selector)
        await self._remove_container(session.container_name)
        await self._remove_volume(session.volume_name)
        if session.metadata_dir.exists():
            for child in sorted(session.metadata_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                else:
                    child.rmdir()
            session.metadata_dir.rmdir()

    async def execute(
        self,
        *,
        selector: ExecSessionSelector,
        command: str,
        cwd: str,
        sync_bundle_bytes: bytes,
        ensure_python: bool = False,
    ) -> ExecResponse:
        if len(sync_bundle_bytes) > self.config.max_sync_bytes:
            raise ExecRunnerError("Incoming sync bundle exceeds the configured size limit.")
        await self.initialize()
        await self._gc_expired_sessions()
        session = await self._ensure_session(selector)
        await self._sync_bundle_into_session(session, sync_bundle_bytes)
        result = await self._run_session_command(
            session,
            command=command,
            cwd=cwd,
            ensure_python=ensure_python,
        )
        diff_bundle_bytes, removed_paths, directory_paths = await self._collect_diff_bundle(session)
        if len(diff_bundle_bytes) > self.config.max_diff_bytes:
            raise ExecRunnerError("Outgoing diff bundle exceeds the configured size limit.")
        await self._write_session_metadata(session)
        return ExecResponse(
            result=result,
            removed_paths=tuple(removed_paths),
            directory_paths=tuple(directory_paths),
            diff_bundle_bytes=diff_bundle_bytes,
        )

    def _session(self, selector: ExecSessionSelector) -> ExecSession:
        session_id = selector.session_id
        container_name = f"nova-exec-{session_id}"
        volume_name = f"nova-exec-session-{session_id}"
        metadata_dir = self.config.state_root / "sessions" / session_id
        metadata_path = metadata_dir / "session.json"
        return ExecSession(
            selector=selector,
            container_name=container_name,
            volume_name=volume_name,
            metadata_dir=metadata_dir,
            metadata_path=metadata_path,
        )

    async def _ensure_session(self, selector: ExecSessionSelector) -> ExecSession:
        session = self._session(selector)
        await asyncio.to_thread(session.metadata_dir.mkdir, parents=True, exist_ok=True)
        await self._ensure_volume(session.volume_name, SESSION_ROOT_IN_CONTAINER)
        exists = await self._container_exists(session.container_name)
        if not exists:
            await self._create_container(session)
        else:
            await self._ensure_container_running(session.container_name)
        return session

    async def _ensure_cache_volume(self) -> None:
        await self._ensure_volume(self.config.shared_cache_volume, CACHE_ROOT_IN_CONTAINER)

    async def _ensure_volume(self, volume_name: str, target_path: Path) -> None:
        exists = await self._volume_exists(volume_name)
        if not exists:
            await self._run_docker("volume", "create", volume_name)
        await self._run_docker(
            "run",
            "--rm",
            "--mount",
            f"source={volume_name},target={target_path}",
            self.config.sandbox_image,
            "bash",
            "-lc",
            (
                f'mkdir -p "{target_path}" '
                f'&& chmod -R 0777 "{target_path}"'
            ),
        )

    async def _create_container(self, session: ExecSession) -> None:
        await self._run_docker(
            "run",
            "-d",
            "--name",
            session.container_name,
            "--network",
            self.config.sandbox_network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nodev,nosuid,size=256m,mode=1777",
            "--mount",
            f"source={session.volume_name},target={SESSION_ROOT_IN_CONTAINER}",
            "--mount",
            f"source={self.config.shared_cache_volume},target={CACHE_ROOT_IN_CONTAINER}",
            "--memory",
            f"{self.config.sandbox_memory_limit_mb}m",
            "--cpus",
            self.config.sandbox_cpu_limit,
            "--pids-limit",
            str(self.config.sandbox_pids_limit),
            "--user",
            "nova",
            "--env",
            f"HOME={SESSION_ROOT_IN_CONTAINER / 'home'}",
            "--env",
            f"PIP_CACHE_DIR={CACHE_ROOT_IN_CONTAINER / 'pip'}",
            "--env",
            f"UV_CACHE_DIR={CACHE_ROOT_IN_CONTAINER / 'uv'}",
            "--env",
            f"npm_config_cache={CACHE_ROOT_IN_CONTAINER / 'npm'}",
            "--env",
            f"HTTP_PROXY={self.config.proxy_url}",
            "--env",
            f"HTTPS_PROXY={self.config.proxy_url}",
            "--env",
            "NO_PROXY=127.0.0.1,localhost",
            self.config.sandbox_image,
            "bash",
            "-lc",
            (
                f'mkdir -p "{WORKSPACE_ROOT_IN_CONTAINER}" '
                f'"{SESSION_ROOT_IN_CONTAINER / "home"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "pip"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "uv"}" '
                f'"{CACHE_ROOT_IN_CONTAINER / "npm"}" '
                "&& exec sleep infinity"
            ),
        )

    async def _ensure_container_running(self, container_name: str) -> None:
        running = await self._container_running(container_name)
        if not running:
            await self._run_docker("start", container_name)

    async def _sync_bundle_into_session(self, session: ExecSession, sync_bundle_bytes: bytes) -> None:
        with tempfile.NamedTemporaryFile(prefix="nova-sync-", suffix=".tar.gz", delete=False) as handle:
            handle.write(sync_bundle_bytes)
            local_path = Path(handle.name)
        try:
            remote_bundle_path = "/tmp/nova-sync.tar.gz"
            await self._run_docker("cp", str(local_path), f"{session.container_name}:{remote_bundle_path}")
            await self._docker_exec(
                session.container_name,
                (
                    f'set -euo pipefail; '
                    f'mkdir -p "{WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME}"; '
                    f'find "{WORKSPACE_ROOT_IN_CONTAINER}" -mindepth 1 -maxdepth 1 ! -name "{RUNNER_INTERNAL_DIRNAME}" -exec rm -rf {{}} +; '
                    f'tar -xzf "{remote_bundle_path}" -C "{WORKSPACE_ROOT_IN_CONTAINER}"; '
                    f'rm -f "{remote_bundle_path}"; '
                    f'python3 - <<\'PY\'\n'
                    f'import hashlib, json, os\n'
                    f'from pathlib import Path\n'
                    f'workspace_root = Path("{WORKSPACE_ROOT_IN_CONTAINER}")\n'
                    f'before_path = Path("{BEFORE_MANIFEST_PATH}")\n'
                    f'read_only_roots = {list(root.lstrip("/") for root in ("/skills", "/inbox", "/history"))!r}\n'
                    f'manifest = {{"files": {{}}, "directories": []}}\n'
                    f'for current_root, dirnames, filenames in os.walk(workspace_root):\n'
                    f'    current = Path(current_root)\n'
                    f'    relative_root = current.relative_to(workspace_root)\n'
                    f'    if relative_root.parts and relative_root.parts[0] == "{RUNNER_INTERNAL_DIRNAME}":\n'
                    f'        dirnames[:] = []\n'
                    f'        continue\n'
                    f'    normalized_dir = "/" if not relative_root.parts else "/" + "/".join(relative_root.parts)\n'
                    f'    if normalized_dir != "/" and not any(normalized_dir == f"/{{root}}" or normalized_dir.startswith(f"/{{root}}/") for root in read_only_roots):\n'
                    f'        manifest["directories"].append(normalized_dir)\n'
                    f'    for filename in filenames:\n'
                    f'        relative_path = relative_root / filename if relative_root.parts else Path(filename)\n'
                    f'        normalized = "/" + relative_path.as_posix()\n'
                    f'        if any(normalized == f"/{{root}}" or normalized.startswith(f"/{{root}}/") for root in read_only_roots):\n'
                    f'            continue\n'
                    f'        with (workspace_root / relative_path).open("rb") as handle:\n'
                    f'            digest = hashlib.sha256(handle.read()).hexdigest()\n'
                    f'        manifest["files"][normalized] = digest\n'
                    f'before_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")\n'
                    f'PY\n'
                    f'for root in "{WORKSPACE_ROOT_IN_CONTAINER / "skills"}" "{WORKSPACE_ROOT_IN_CONTAINER / "inbox"}" "{WORKSPACE_ROOT_IN_CONTAINER / "history"}"; do '
                    f'  if [ -e "$root" ]; then chmod -R a-w "$root" || true; fi; '
                    f'done'
                ),
            )
        finally:
            local_path.unlink(missing_ok=True)

    async def _run_session_command(
        self,
        session: ExecSession,
        *,
        command: str,
        cwd: str,
        ensure_python: bool = False,
    ) -> SandboxShellResult:
        del ensure_python
        persisted_env = await self._load_persisted_env(session.container_name)
        env = self._base_environment()
        env.update({key: value for key, value in persisted_env.items() if not key.startswith("NOVA_")})
        normalized_cwd = normalize_sandbox_path(cwd, cwd="/")
        if normalized_cwd in {"/skills", "/inbox", "/history", "/memory", "/webdav"}:
            normalized_cwd = "/"
        rewritten_command = rewrite_shell_command_for_workspace(command, WORKSPACE_ROOT_IN_CONTAINER)
        rendered_env = encode_environment_script(env)
        command_script = "\n".join(
            [
                "set +e",
                f'cd "{WORKSPACE_ROOT_IN_CONTAINER if normalized_cwd == "/" else WORKSPACE_ROOT_IN_CONTAINER / normalized_cwd.lstrip("/")}" || exit 1',
                rendered_env,
                rewritten_command,
                "status=$?",
                f'pwd > "{CWD_PATH}"',
                f'python3 - <<\'PYENV\' > "{ENV_PATH}"',
                "import json, os",
                "print(json.dumps(dict(os.environ), ensure_ascii=False))",
                "PYENV",
                "exit $status",
            ]
        )
        await self._write_text_into_container(session.container_name, COMMAND_PATH, command_script)
        stdout, stderr, returncode = await self._docker_exec_capture(
            session.container_name,
            f'set -euo pipefail; bash -lc \'. "{COMMAND_PATH}"\'',
        )
        await self._cleanup_processes(session.container_name)
        cwd_after = await self._read_text_from_container(session.container_name, CWD_PATH, default="/")
        normalized_cwd_after = vfs_path_for_workspace_path(
            WORKSPACE_ROOT_IN_CONTAINER,
            cwd_after.strip(),
        )
        return SandboxShellResult(
            stdout=rewrite_output_paths_from_workspace(stdout, WORKSPACE_ROOT_IN_CONTAINER),
            stderr=rewrite_output_paths_from_workspace(stderr, WORKSPACE_ROOT_IN_CONTAINER),
            status=int(returncode or 0),
            cwd_after=normalized_cwd_after,
        )

    async def _collect_diff_bundle(self, session: ExecSession) -> tuple[bytes, list[str], list[str]]:
        metadata_text = await self._docker_exec_stdout(
            session.container_name,
            (
                f'set -euo pipefail; '
                f'python3 - <<\'PY\'\n'
                f'import hashlib, json, os, tarfile\n'
                f'from pathlib import Path\n'
                f'workspace_root = Path("{WORKSPACE_ROOT_IN_CONTAINER}")\n'
                f'before_path = Path("{BEFORE_MANIFEST_PATH}")\n'
                f'diff_path = Path("{DIFF_BUNDLE_PATH}")\n'
                f'metadata_path = Path("{DIFF_METADATA_PATH}")\n'
                f'if before_path.exists():\n'
                f'    before = json.loads(before_path.read_text(encoding="utf-8"))\n'
                f'else:\n'
                f'    before = {{"files": {{}}, "directories": []}}\n'
                f'read_only_roots = {list(root.lstrip("/") for root in ("/skills", "/inbox", "/history"))!r}\n'
                f'after_files = {{}}\n'
                f'directories = []\n'
                f'for current_root, dirnames, filenames in os.walk(workspace_root):\n'
                f'    current = Path(current_root)\n'
                f'    relative_root = current.relative_to(workspace_root)\n'
                f'    if relative_root.parts and relative_root.parts[0] == "{RUNNER_INTERNAL_DIRNAME}":\n'
                f'        dirnames[:] = []\n'
                f'        continue\n'
                f'    normalized_dir = "/" if not relative_root.parts else "/" + "/".join(relative_root.parts)\n'
                f'    if normalized_dir != "/" and not any(normalized_dir == f"/{{root}}" or normalized_dir.startswith(f"/{{root}}/") for root in read_only_roots):\n'
                f'        directories.append(normalized_dir)\n'
                f'    for filename in filenames:\n'
                f'        relative_path = relative_root / filename if relative_root.parts else Path(filename)\n'
                f'        normalized = "/" + relative_path.as_posix()\n'
                f'        if any(normalized == f"/{{root}}" or normalized.startswith(f"/{{root}}/") for root in read_only_roots):\n'
                f'            continue\n'
                f'        with (workspace_root / relative_path).open("rb") as handle:\n'
                f'            digest = hashlib.sha256(handle.read()).hexdigest()\n'
                f'        after_files[normalized] = digest\n'
                f'changed = sorted(path for path, digest in after_files.items() if before.get("files", {{}}).get(path) != digest)\n'
                f'removed = sorted(path for path in before.get("files", {{}}).keys() if path not in after_files)\n'
                f'with tarfile.open(diff_path, "w:gz") as archive:\n'
                f'    for normalized in changed:\n'
                f'        source = workspace_root / normalized.lstrip("/")\n'
                f'        archive.add(source, arcname=normalized.lstrip("/"))\n'
                f'metadata = {{"removed_paths": removed, "directory_paths": sorted(set(directories)), "changed_paths": changed}}\n'
                f'metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")\n'
                f'print(json.dumps(metadata, ensure_ascii=False))\n'
                f'PY'
            ),
        )
        metadata = json.loads(metadata_text or "{}")
        with tempfile.NamedTemporaryFile(prefix="nova-diff-", suffix=".tar.gz", delete=False) as handle:
            local_path = Path(handle.name)
        try:
            await self._run_docker("cp", f"{session.container_name}:{DIFF_BUNDLE_PATH}", str(local_path))
            diff_bundle_bytes = await asyncio.to_thread(local_path.read_bytes)
        finally:
            local_path.unlink(missing_ok=True)
            await self._docker_exec(
                session.container_name,
                f'rm -f "{DIFF_BUNDLE_PATH}" "{DIFF_METADATA_PATH}"',
            )
        return (
            diff_bundle_bytes,
            list(metadata.get("removed_paths") or []),
            list(metadata.get("directory_paths") or []),
        )

    async def _load_persisted_env(self, container_name: str) -> dict[str, str]:
        text = await self._read_text_from_container(container_name, ENV_PATH, default="")
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _base_environment(self) -> dict[str, str]:
        path = ":".join(
            [
                str(SESSION_ROOT_IN_CONTAINER / "home" / ".local" / "bin"),
                "/usr/local/sbin",
                "/usr/local/bin",
                "/usr/sbin",
                "/usr/bin",
                "/sbin",
                "/bin",
            ]
        )
        return {
            "HOME": str(SESSION_ROOT_IN_CONTAINER / "home"),
            "PATH": path,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PIP_CACHE_DIR": str(CACHE_ROOT_IN_CONTAINER / "pip"),
            "UV_CACHE_DIR": str(CACHE_ROOT_IN_CONTAINER / "uv"),
            "npm_config_cache": str(CACHE_ROOT_IN_CONTAINER / "npm"),
            "HTTP_PROXY": self.config.proxy_url,
            "HTTPS_PROXY": self.config.proxy_url,
            "NO_PROXY": "127.0.0.1,localhost",
        }

    async def _cleanup_processes(self, container_name: str) -> None:
        await self._docker_exec(
            container_name,
            (
                "set +e; "
                "pids=$(ps -eo pid= | awk '$1 != 1 {print $1}'); "
                "if [ -n \"$pids\" ]; then kill -TERM $pids 2>/dev/null || true; sleep 1; fi; "
                "pids=$(ps -eo pid= | awk '$1 != 1 {print $1}'); "
                "if [ -n \"$pids\" ]; then kill -KILL $pids 2>/dev/null || true; fi"
            ),
        )

    async def _write_session_metadata(self, session: ExecSession) -> None:
        payload = {
            "session_id": session.selector.session_id,
            "last_used_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "container_name": session.container_name,
            "volume_name": session.volume_name,
        }
        await asyncio.to_thread(session.metadata_path.write_text, json.dumps(payload, ensure_ascii=False), "utf-8")

    async def _gc_expired_sessions(self) -> None:
        sessions_root = self.config.state_root / "sessions"
        if not sessions_root.exists():
            return
        now = dt.datetime.now(dt.timezone.utc)
        for metadata_path in sessions_root.glob("*/session.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                last_used = dt.datetime.fromisoformat(str(data.get("last_used_at") or ""))
            except (OSError, TypeError, ValueError):
                continue
            if last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=dt.timezone.utc)
            if (now - last_used.astimezone(dt.timezone.utc)).total_seconds() <= self.config.session_ttl_seconds:
                continue
            selector = ExecSessionSelector(
                user_id=str(data.get("session_id") or metadata_path.parent.name).split("--")[0].replace("user-", "", 1),
                thread_id="expired",
                agent_id="expired",
            )
            session = ExecSession(
                selector=selector,
                container_name=str(data.get("container_name") or ""),
                volume_name=str(data.get("volume_name") or ""),
                metadata_dir=metadata_path.parent,
                metadata_path=metadata_path,
            )
            if session.container_name:
                await self._remove_container(session.container_name)
            if session.volume_name:
                await self._remove_volume(session.volume_name)
            for child in sorted(session.metadata_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                else:
                    child.rmdir()
            session.metadata_dir.rmdir()

    async def _container_exists(self, container_name: str) -> bool:
        try:
            await self._run_docker("inspect", container_name)
        except ExecRunnerError:
            return False
        return True

    async def _container_running(self, container_name: str) -> bool:
        try:
            stdout = await self._run_docker("inspect", "--format", "{{.State.Running}}", container_name)
        except ExecRunnerError:
            return False
        return stdout.strip() == "true"

    async def _volume_exists(self, volume_name: str) -> bool:
        try:
            await self._run_docker("volume", "inspect", volume_name)
        except ExecRunnerError:
            return False
        return True

    async def _remove_container(self, container_name: str) -> None:
        if not container_name:
            return
        try:
            await self._run_docker("rm", "-f", container_name)
        except ExecRunnerError:
            return

    async def _remove_volume(self, volume_name: str) -> None:
        if not volume_name:
            return
        try:
            await self._run_docker("volume", "rm", "-f", volume_name)
        except ExecRunnerError:
            return

    async def _write_text_into_container(self, container_name: str, path: Path, text: str) -> None:
        with tempfile.NamedTemporaryFile(prefix="nova-runner-script-", suffix=".txt", delete=False) as handle:
            handle.write(text.encode("utf-8"))
            local_path = Path(handle.name)
        try:
            await self._run_docker("cp", str(local_path), f"{container_name}:{path}")
        finally:
            local_path.unlink(missing_ok=True)

    async def _read_text_from_container(self, container_name: str, path: Path, *, default: str = "") -> str:
        try:
            return await self._docker_exec_stdout(
                container_name,
                f'if [ -f "{path}" ]; then cat "{path}"; fi',
            )
        except ExecRunnerError:
            return default

    async def _docker_exec_stdout(self, container_name: str, command: str) -> str:
        stdout, _stderr, _status = await self._docker_exec_capture(container_name, command)
        return stdout

    async def _docker_exec(self, container_name: str, command: str) -> None:
        _stdout, stderr, status = await self._docker_exec_capture(container_name, command)
        if status != 0:
            raise ExecRunnerError(stderr.strip() or f"Docker exec failed with status {status}.")

    async def _docker_exec_capture(self, container_name: str, command: str) -> tuple[str, str, int]:
        return await self._run_process(
            [
                "docker",
                "exec",
                "-u",
                "nova",
                container_name,
                "bash",
                "-lc",
                command,
            ],
            timeout=self.config.command_timeout_seconds,
        )

    async def _run_docker(self, *args: str) -> str:
        stdout, stderr, status = await self._run_process(
            ["docker", *args],
            timeout=self.config.command_timeout_seconds,
        )
        if status != 0:
            raise ExecRunnerError(stderr.strip() or f"Docker command failed: {' '.join(args)}")
        return stdout

    async def _run_process(self, command: list[str], *, timeout: int) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ExecRunnerError("The sandbox command timed out.") from exc
        return (
            (stdout or b"").decode("utf-8", errors="replace"),
            (stderr or b"").decode("utf-8", errors="replace"),
            int(process.returncode or 0),
        )


def require_valid_runner_token(expected_token: str, provided_token: str) -> None:
    if not expected_token:
        raise ExecRunnerError("The exec runner shared token is not configured.")
    if not secrets.compare_digest(str(expected_token), str(provided_token or "")):
        raise ExecRunnerError("Invalid exec runner credentials.")
