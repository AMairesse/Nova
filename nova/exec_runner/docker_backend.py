from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import secrets
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from nova.exec_runner.shared import (
    ExecRunnerError,
    ExecSessionSelector,
    PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE,
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
SITECUSTOMIZE_PATH = WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME / "sitecustomize.py"
MANAGED_LABEL_KEY = "nova.exec_runner.managed"
RESOURCE_LABEL_KEY = "nova.exec_runner.resource"
SESSION_ID_LABEL_KEY = "nova.exec_runner.session_id"
USER_ID_LABEL_KEY = "nova.exec_runner.user_id"
THREAD_ID_LABEL_KEY = "nova.exec_runner.thread_id"
AGENT_ID_LABEL_KEY = "nova.exec_runner.agent_id"
SESSION_RESOURCE_LABEL_VALUE = "session"
CACHE_RESOURCE_LABEL_VALUE = "cache"
CACHE_RECENT_GUARD_SECONDS = 3600
CACHE_VOLUME_PREFIX = "nova-exec-cache-user-"

_CACHE_PRUNE_SCRIPT = textwrap.dedent(
    """\
    import json
    import os
    import sys
    import time
    from pathlib import Path

    cache_root = Path(sys.argv[1])
    cache_max_bytes = int(sys.argv[2])
    cache_target_bytes = int(sys.argv[3])
    cache_max_age_days = int(sys.argv[4])
    recent_guard_seconds = int(sys.argv[5])

    files_removed = 0
    directories_removed = 0
    bytes_reclaimed = 0

    if not cache_root.exists():
        print(json.dumps(
            {
                "files_removed": 0,
                "directories_removed": 0,
                "bytes_reclaimed": 0,
                "errors": 0,
            }
        ))
        raise SystemExit(0)

    now_ts = time.time()
    max_age_seconds = cache_max_age_days * 24 * 60 * 60

    def collect_files():
        collected = []
        for current_root, _dirnames, filenames in os.walk(cache_root):
            for filename in filenames:
                path = Path(current_root) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                collected.append((path, int(stat.st_size), float(stat.st_mtime)))
        return collected

    def delete_file(path, size):
        global files_removed, bytes_reclaimed
        path.unlink(missing_ok=True)
        files_removed += 1
        bytes_reclaimed += max(size, 0)

    files = collect_files()
    total_size = sum(size for _path, size, _mtime in files)
    recent_cutoff = now_ts - recent_guard_seconds
    age_cutoff = now_ts - max_age_seconds

    remaining = []
    for path, size, mtime in files:
        if mtime <= age_cutoff and mtime <= recent_cutoff:
            delete_file(path, size)
            total_size -= size
        else:
            remaining.append((path, size, mtime))

    if total_size > cache_max_bytes:
        for path, size, mtime in sorted(remaining, key=lambda item: item[2]):
            if total_size <= cache_target_bytes:
                break
            if mtime > recent_cutoff:
                continue
            delete_file(path, size)
            total_size -= size

    for current_root, dirnames, _filenames in os.walk(cache_root, topdown=False):
        for dirname in dirnames:
            directory = Path(current_root) / dirname
            try:
                directory.rmdir()
            except OSError:
                continue
            directories_removed += 1

    print(json.dumps(
        {
            "files_removed": files_removed,
            "directories_removed": directories_removed,
            "bytes_reclaimed": bytes_reclaimed,
            "errors": 0,
        }
    ))
    """
)

logger = logging.getLogger(__name__)


def _render_shell_export(name: str, value: str) -> str:
    safe_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'export {name}="{safe_value}"'


@dataclass(slots=True, frozen=True)
class ExecRunnerConfig:
    shared_token: str
    state_root: Path
    session_ttl_seconds: int
    gc_interval_seconds: int
    sandbox_image: str
    sandbox_network: str
    sandbox_memory_limit_mb: int
    sandbox_cpu_limit: str
    sandbox_pids_limit: int
    max_sync_bytes: int
    max_diff_bytes: int
    proxy_url: str
    cache_max_bytes: int
    cache_target_bytes: int
    cache_max_age_days: int
    sandbox_no_new_privileges: bool = True
    command_timeout_seconds: int = 300


@dataclass(slots=True, frozen=True)
class ExecSession:
    selector: ExecSessionSelector
    container_name: str
    volume_name: str
    metadata_dir: Path
    metadata_path: Path


@dataclass(slots=True, frozen=True)
class ManagedSessionRecord:
    session_id: str
    user_id: str
    thread_id: str
    agent_id: str
    last_used_at: dt.datetime | None
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
    session_ttl_seconds = max(int(os.getenv("EXEC_RUNNER_SESSION_TTL_SECONDS", "14400")), 60)
    gc_interval_seconds = max(int(os.getenv("EXEC_RUNNER_GC_INTERVAL_SECONDS", "900")), 60)
    sandbox_image = str(os.getenv("EXEC_RUNNER_SANDBOX_IMAGE", "amairesse/nova:latest")).strip()
    sandbox_network = str(os.getenv("EXEC_RUNNER_SANDBOX_NETWORK", "exec-sandbox-net")).strip()
    sandbox_memory_limit_mb = max(int(os.getenv("EXEC_RUNNER_SANDBOX_MEMORY_LIMIT_MB", "1024")), 256)
    sandbox_cpu_limit = str(os.getenv("EXEC_RUNNER_SANDBOX_CPU_LIMIT", "1.0")).strip() or "1.0"
    sandbox_pids_limit = max(int(os.getenv("EXEC_RUNNER_SANDBOX_PIDS_LIMIT", "256")), 64)
    sandbox_no_new_privileges = (
        str(os.getenv("EXEC_RUNNER_SANDBOX_NO_NEW_PRIVILEGES", "true")).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    max_sync_bytes = max(int(os.getenv("EXEC_RUNNER_MAX_SYNC_BYTES", str(50 * 1024 * 1024))), 1024 * 1024)
    max_diff_bytes = max(int(os.getenv("EXEC_RUNNER_MAX_DIFF_BYTES", str(50 * 1024 * 1024))), 1024 * 1024)
    cache_max_bytes = max(int(os.getenv("EXEC_RUNNER_CACHE_MAX_BYTES", str(5 * 1024 * 1024 * 1024))), 1024 * 1024)
    cache_target_bytes = max(int(os.getenv("EXEC_RUNNER_CACHE_TARGET_BYTES", str(3 * 1024 * 1024 * 1024))), 1024 * 1024)
    cache_target_bytes = min(cache_target_bytes, cache_max_bytes)
    cache_max_age_days = max(int(os.getenv("EXEC_RUNNER_CACHE_MAX_AGE_DAYS", "14")), 1)
    proxy_port = max(int(os.getenv("EXEC_RUNNER_PROXY_PORT", "8091")), 1)
    proxy_url = str(os.getenv("EXEC_RUNNER_PROXY_URL", f"http://exec-runner:{proxy_port}")).strip()
    command_timeout_seconds = max(int(os.getenv("EXEC_RUNNER_COMMAND_TIMEOUT_SECONDS", "300")), 5)
    return ExecRunnerConfig(
        shared_token=shared_token,
        state_root=state_root,
        session_ttl_seconds=session_ttl_seconds,
        gc_interval_seconds=gc_interval_seconds,
        sandbox_image=sandbox_image,
        sandbox_network=sandbox_network,
        sandbox_memory_limit_mb=sandbox_memory_limit_mb,
        sandbox_cpu_limit=sandbox_cpu_limit,
        sandbox_pids_limit=sandbox_pids_limit,
        sandbox_no_new_privileges=sandbox_no_new_privileges,
        max_sync_bytes=max_sync_bytes,
        max_diff_bytes=max_diff_bytes,
        proxy_url=proxy_url,
        cache_max_bytes=cache_max_bytes,
        cache_target_bytes=cache_target_bytes,
        cache_max_age_days=cache_max_age_days,
        command_timeout_seconds=command_timeout_seconds,
    )


class DockerExecRunnerBackend:
    def __init__(self, config: ExecRunnerConfig):
        self.config = config
        self._initialized = False
        self._maintenance_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self.config.state_root.mkdir, parents=True, exist_ok=True)
        await self._run_docker("version", "--format", "{{json .Server.Version}}")
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
        await self._delete_session_artifacts(
            ManagedSessionRecord(
                session_id=selector.session_id,
                user_id=str(selector.user_id),
                thread_id=str(selector.thread_id),
                agent_id=str(selector.agent_id),
                last_used_at=None,
                container_name=session.container_name,
                volume_name=session.volume_name,
                metadata_dir=session.metadata_dir,
                metadata_path=session.metadata_path,
            )
        )

    async def delete_sessions_for_thread(self, *, user_id: int | str, thread_id: int | str) -> int:
        await self.initialize()
        removed_session_ids: set[str] = set()
        for record in await self._load_session_records():
            if record.user_id != str(user_id) or record.thread_id != str(thread_id):
                continue
            await self._delete_session_artifacts(record)
            removed_session_ids.add(record.session_id)

        containers, volumes = await self._list_managed_resources()
        removed_container_names: set[str] = set()
        removed_volume_names: set[str] = set()

        for resource in containers:
            labels = resource.get("labels") or {}
            if str(labels.get(USER_ID_LABEL_KEY) or "") != str(user_id):
                continue
            if str(labels.get(THREAD_ID_LABEL_KEY) or "") != str(thread_id):
                continue
            name = str(resource.get("name") or "").strip()
            session_id = str(labels.get(SESSION_ID_LABEL_KEY) or self._session_id_from_container_name(name) or "").strip()
            if name and await self._remove_container(name):
                removed_container_names.add(name)
            if session_id:
                removed_session_ids.add(session_id)

        for resource in volumes:
            labels = resource.get("labels") or {}
            if str(labels.get(USER_ID_LABEL_KEY) or "") != str(user_id):
                continue
            if str(labels.get(THREAD_ID_LABEL_KEY) or "") != str(thread_id):
                continue
            name = str(resource.get("name") or "").strip()
            session_id = str(labels.get(SESSION_ID_LABEL_KEY) or self._session_id_from_volume_name(name) or "").strip()
            if name and await self._remove_volume(name):
                removed_volume_names.add(name)
            if session_id:
                removed_session_ids.add(session_id)

        if removed_session_ids or removed_container_names or removed_volume_names:
            logger.info(
                "exec-runner removed %s warm session(s) for user=%s thread=%s",
                len(removed_session_ids) or max(len(removed_container_names), len(removed_volume_names)),
                user_id,
                thread_id,
            )
        return len(removed_session_ids) or max(len(removed_container_names), len(removed_volume_names))

    async def run_maintenance_cycle(self) -> dict[str, int]:
        await self.initialize()
        async with self._maintenance_lock:
            expired_sessions_removed = await self._gc_expired_sessions_locked()
            orphaned = await self._sweep_orphaned_resources_locked()
            cache_prune = await self._prune_cache_volumes_locked()

        summary = {
            "expired_sessions_removed": expired_sessions_removed,
            "orphaned_containers_removed": orphaned["containers_removed"],
            "orphaned_volumes_removed": orphaned["volumes_removed"],
            "cache_files_removed": cache_prune["files_removed"],
            "cache_directories_removed": cache_prune["directories_removed"],
            "cache_bytes_reclaimed": cache_prune["bytes_reclaimed"],
            "errors": orphaned["errors"] + cache_prune["errors"],
        }
        logger.info(
            "exec-runner maintenance: expired_sessions=%s orphaned_containers=%s orphaned_volumes=%s cache_files=%s cache_dirs=%s cache_bytes=%s errors=%s",
            summary["expired_sessions_removed"],
            summary["orphaned_containers_removed"],
            summary["orphaned_volumes_removed"],
            summary["cache_files_removed"],
            summary["cache_directories_removed"],
            summary["cache_bytes_reclaimed"],
            summary["errors"],
        )
        return summary

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
        await self._write_session_metadata(session)
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
        await self._ensure_volume(
            session.volume_name,
            SESSION_ROOT_IN_CONTAINER,
            labels=self._session_resource_labels(session.selector),
        )
        await self._ensure_user_cache_volume(selector.user_id)
        exists = await self._container_exists(session.container_name)
        expected_cache_volume = self._cache_volume_name(selector.user_id)
        if exists and not await self._container_has_mount(
            session.container_name,
            source=expected_cache_volume,
            destination=str(CACHE_ROOT_IN_CONTAINER),
        ):
            await self._remove_container(session.container_name)
            exists = False
        if not exists:
            await self._create_container(session)
        else:
            await self._ensure_container_running(session.container_name)
        return session

    def _cache_volume_name(self, user_id: int | str) -> str:
        return f"{CACHE_VOLUME_PREFIX}{ExecSessionSelector._slug(user_id)}"

    def _cache_volume_labels(self, user_id: int | str) -> dict[str, str]:
        return {
            MANAGED_LABEL_KEY: "true",
            RESOURCE_LABEL_KEY: CACHE_RESOURCE_LABEL_VALUE,
            USER_ID_LABEL_KEY: str(user_id),
        }

    async def _ensure_user_cache_volume(self, user_id: int | str) -> None:
        await self._ensure_volume(
            self._cache_volume_name(user_id),
            CACHE_ROOT_IN_CONTAINER,
            labels=self._cache_volume_labels(user_id),
        )

    async def _ensure_volume(
        self,
        volume_name: str,
        target_path: Path,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        exists = await self._volume_exists(volume_name)
        if not exists:
            create_args = ["volume", "create"]
            for key, value in sorted((labels or {}).items()):
                create_args.extend(["--label", f"{key}={value}"])
            create_args.append(volume_name)
            await self._run_docker(*create_args)
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
                f'&& chown -R nova:nova "{target_path}" '
                f'&& chmod -R u+rwX,go-rwx "{target_path}"'
            ),
        )

    async def _create_container(self, session: ExecSession) -> None:
        labels = self._session_resource_labels(session.selector)
        docker_args = [
            "run",
            "-d",
            "--name",
            session.container_name,
            "--network",
            self.config.sandbox_network,
            "--read-only",
            "--cap-drop",
            "ALL",
        ]
        for key, value in sorted(labels.items()):
            docker_args.extend(["--label", f"{key}={value}"])
        if self.config.sandbox_no_new_privileges:
            docker_args.extend(["--security-opt", "no-new-privileges"])
        docker_args.extend(
            [
                "--tmpfs",
                "/tmp:rw,nodev,nosuid,size=256m,mode=1777",
                "--mount",
                f"source={session.volume_name},target={SESSION_ROOT_IN_CONTAINER}",
                "--mount",
                f"source={self._cache_volume_name(session.selector.user_id)},target={CACHE_ROOT_IN_CONTAINER}",
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
            ]
        )
        await self._run_docker(*docker_args)

    async def _ensure_container_running(self, container_name: str) -> None:
        running = await self._container_running(container_name)
        if not running:
            await self._run_docker("start", container_name)

    async def _sync_bundle_into_session(self, session: ExecSession, sync_bundle_bytes: bytes) -> None:
        remote_bundle_path = Path("/tmp/nova-sync.tar.gz")
        await self._write_bytes_into_container(
            session.container_name,
            remote_bundle_path,
            sync_bundle_bytes,
        )
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

    async def _run_session_command(
        self,
        session: ExecSession,
        *,
        command: str,
        cwd: str,
        ensure_python: bool = False,
    ) -> SandboxShellResult:
        persisted_env = await self._load_persisted_env(session.container_name)
        env = self._base_environment()
        env.update({key: value for key, value in persisted_env.items() if not key.startswith("NOVA_")})
        restore_python_env_lines: list[str] = []
        if ensure_python:
            internal_python_path = str(WORKSPACE_ROOT_IN_CONTAINER / RUNNER_INTERNAL_DIRNAME)
            existing_python_path = str(env.get("PYTHONPATH") or "").strip()
            env["PYTHONPATH"] = (
                internal_python_path
                if not existing_python_path
                else f"{internal_python_path}:{existing_python_path}"
            )
            env["NOVA_WORKSPACE_ROOT"] = str(WORKSPACE_ROOT_IN_CONTAINER)
            restore_python_env_lines.append(
                _render_shell_export("PYTHONPATH", existing_python_path)
                if existing_python_path
                else "unset PYTHONPATH"
            )
            restore_python_env_lines.append("unset NOVA_WORKSPACE_ROOT")
        normalized_cwd = normalize_sandbox_path(cwd, cwd="/")
        if normalized_cwd in {"/skills", "/inbox", "/history", "/memory", "/webdav"}:
            normalized_cwd = "/"
        rewritten_command = rewrite_shell_command_for_workspace(command, WORKSPACE_ROOT_IN_CONTAINER)
        rendered_env = encode_environment_script(env)
        if ensure_python:
            await self._write_text_into_container(
                session.container_name,
                SITECUSTOMIZE_PATH,
                PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE,
            )
        command_script = "\n".join(
            [
                "set +e",
                f'cd "{WORKSPACE_ROOT_IN_CONTAINER if normalized_cwd == "/" else WORKSPACE_ROOT_IN_CONTAINER / normalized_cwd.lstrip("/")}" || exit 1',
                rendered_env,
                rewritten_command,
                "status=$?",
                *restore_python_env_lines,
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
                "set +e; self_pid=$$; "
                "pids=$(ps -eo pid= | awk -v self=\"$self_pid\" '$1 != 1 && $1 != self {print $1}'); "
                "if [ -n \"$pids\" ]; then kill -TERM $pids 2>/dev/null || true; sleep 1; fi; "
                "pids=$(ps -eo pid= | awk -v self=\"$self_pid\" '$1 != 1 && $1 != self {print $1}'); "
                "if [ -n \"$pids\" ]; then kill -KILL $pids 2>/dev/null || true; fi; "
                "exit 0"
            ),
        )

    def _session_resource_labels(self, selector: ExecSessionSelector) -> dict[str, str]:
        return {
            MANAGED_LABEL_KEY: "true",
            RESOURCE_LABEL_KEY: SESSION_RESOURCE_LABEL_VALUE,
            SESSION_ID_LABEL_KEY: selector.session_id,
            USER_ID_LABEL_KEY: str(selector.user_id),
            THREAD_ID_LABEL_KEY: str(selector.thread_id),
            AGENT_ID_LABEL_KEY: str(selector.agent_id),
        }

    async def _write_session_metadata(self, session: ExecSession, *, timestamp: dt.datetime | None = None) -> None:
        written_at = (timestamp or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        payload = {
            "session_id": session.selector.session_id,
            "user_id": str(session.selector.user_id),
            "thread_id": str(session.selector.thread_id),
            "agent_id": str(session.selector.agent_id),
            "last_used_at": written_at.isoformat(),
            "container_name": session.container_name,
            "volume_name": session.volume_name,
        }
        await asyncio.to_thread(session.metadata_path.write_text, json.dumps(payload, ensure_ascii=False), "utf-8")

    async def _gc_expired_sessions(self) -> None:
        async with self._maintenance_lock:
            await self._gc_expired_sessions_locked()

    async def _gc_expired_sessions_locked(self) -> int:
        sessions_root = self.config.state_root / "sessions"
        if not sessions_root.exists():
            return 0
        now = dt.datetime.now(dt.timezone.utc)
        removed_count = 0
        for record in await self._load_session_records():
            if record.last_used_at is None:
                continue
            last_used = record.last_used_at.astimezone(dt.timezone.utc)
            if (now - last_used).total_seconds() <= self.config.session_ttl_seconds:
                continue
            await self._delete_session_artifacts(record)
            removed_count += 1
        return removed_count

    async def _load_session_records(self) -> list[ManagedSessionRecord]:
        sessions_root = self.config.state_root / "sessions"
        if not sessions_root.exists():
            return []
        records: list[ManagedSessionRecord] = []
        for metadata_path in sorted(sessions_root.glob("*/session.json")):
            record = await asyncio.to_thread(self._load_session_record_from_path, metadata_path)
            if record is not None:
                records.append(record)
        return records

    def _load_session_record_from_path(self, metadata_path: Path) -> ManagedSessionRecord | None:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None

        session_id = str(data.get("session_id") or metadata_path.parent.name).strip() or metadata_path.parent.name
        session_parts = self._parse_session_id(session_id)
        last_used_raw = str(data.get("last_used_at") or "").strip()
        last_used: dt.datetime | None = None
        if last_used_raw:
            try:
                parsed = dt.datetime.fromisoformat(last_used_raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                last_used = parsed.astimezone(dt.timezone.utc)
            except ValueError:
                last_used = None
        return ManagedSessionRecord(
            session_id=session_id,
            user_id=str(data.get("user_id") or session_parts.get("user") or ""),
            thread_id=str(data.get("thread_id") or session_parts.get("thread") or ""),
            agent_id=str(data.get("agent_id") or session_parts.get("agent") or ""),
            last_used_at=last_used,
            container_name=str(data.get("container_name") or ""),
            volume_name=str(data.get("volume_name") or ""),
            metadata_dir=metadata_path.parent,
            metadata_path=metadata_path,
        )

    def _parse_session_id(self, session_id: str) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                item.split("-", 1)
                for item in str(session_id or "").split("--")
                if "-" in item
            )
        }

    def _session_id_from_container_name(self, container_name: str) -> str:
        prefix = "nova-exec-"
        text = str(container_name or "").strip()
        if text.startswith(prefix):
            return text[len(prefix):]
        return ""

    def _session_id_from_volume_name(self, volume_name: str) -> str:
        prefix = "nova-exec-session-"
        text = str(volume_name or "").strip()
        if text.startswith(prefix):
            return text[len(prefix):]
        return ""

    async def _delete_session_artifacts(self, record: ManagedSessionRecord) -> None:
        if record.container_name:
            await self._remove_container(record.container_name)
        if record.volume_name:
            await self._remove_volume(record.volume_name)
        await asyncio.to_thread(self._remove_metadata_dir, record.metadata_dir)

    def _remove_metadata_dir(self, metadata_dir: Path) -> None:
        if not metadata_dir.exists():
            return
        for child in sorted(metadata_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            else:
                child.rmdir()
        metadata_dir.rmdir()

    async def _list_managed_resources(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        container_ids = set(await self._list_docker_items("ps", "-aq", "--filter", f"label={MANAGED_LABEL_KEY}=true"))
        container_ids.update(await self._list_docker_items("ps", "-aq", "--filter", "name=nova-exec-"))

        volume_names = set(
            name
            for name in await self._list_docker_items(
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label={MANAGED_LABEL_KEY}=true",
                "--filter",
                f"label={RESOURCE_LABEL_KEY}={SESSION_RESOURCE_LABEL_VALUE}",
            )
        )
        volume_names.update(
            name
            for name in await self._list_docker_items("volume", "ls", "-q", "--filter", "name=nova-exec-session-")
        )

        containers = await self._inspect_containers(sorted(container_ids))
        volumes = await self._inspect_volumes(sorted(volume_names))
        return containers, volumes

    async def _list_managed_cache_volumes(self) -> list[dict[str, str]]:
        volume_names = set(
            await self._list_docker_items(
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label={MANAGED_LABEL_KEY}=true",
                "--filter",
                f"label={RESOURCE_LABEL_KEY}={CACHE_RESOURCE_LABEL_VALUE}",
            )
        )
        volumes = await self._inspect_volumes(sorted(volume_names))
        return [
            resource
            for resource in volumes
            if str((resource.get("labels") or {}).get(RESOURCE_LABEL_KEY) or "") == CACHE_RESOURCE_LABEL_VALUE
        ]

    async def _list_docker_items(self, *args: str) -> list[str]:
        try:
            stdout = await self._run_docker(*args)
        except ExecRunnerError:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    async def _inspect_containers(self, container_ids: list[str]) -> list[dict[str, str]]:
        if not container_ids:
            return []
        try:
            raw = await self._run_docker("inspect", *container_ids)
        except ExecRunnerError:
            return []
        try:
            payload = json.loads(raw or "[]")
        except ValueError:
            return []
        resources: list[dict[str, str]] = []
        for item in payload if isinstance(payload, list) else []:
            labels = ((item.get("Config") or {}).get("Labels") or {}) if isinstance(item, dict) else {}
            resources.append(
                {
                    "name": str(item.get("Name") or "").lstrip("/"),
                    "created_at": str(item.get("Created") or ""),
                    "session_id": str(labels.get(SESSION_ID_LABEL_KEY) or ""),
                    "labels": labels,
                }
            )
        return resources

    async def _inspect_volumes(self, volume_names: list[str]) -> list[dict[str, str]]:
        if not volume_names:
            return []
        try:
            raw = await self._run_docker("volume", "inspect", *volume_names)
        except ExecRunnerError:
            return []
        try:
            payload = json.loads(raw or "[]")
        except ValueError:
            return []
        resources: list[dict[str, str]] = []
        for item in payload if isinstance(payload, list) else []:
            labels = (item.get("Labels") or {}) if isinstance(item, dict) else {}
            resources.append(
                {
                    "name": str(item.get("Name") or ""),
                    "created_at": str(item.get("CreatedAt") or ""),
                    "session_id": str(labels.get(SESSION_ID_LABEL_KEY) or ""),
                    "labels": labels,
                }
            )
        return resources

    def _is_resource_older_than_ttl(self, created_at: str) -> bool:
        text = str(created_at or "").strip()
        if not text:
            return True
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        age_seconds = (dt.datetime.now(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)).total_seconds()
        return age_seconds > self.config.session_ttl_seconds

    async def _sweep_orphaned_resources_locked(self) -> dict[str, int]:
        records = {record.session_id for record in await self._load_session_records()}
        containers, volumes = await self._list_managed_resources()
        containers_removed = 0
        volumes_removed = 0
        errors = 0

        for resource in containers:
            session_id = str(resource.get("session_id") or self._session_id_from_container_name(str(resource.get("name") or "")) or "").strip()
            if session_id and session_id in records:
                continue
            if session_id and not self._is_resource_older_than_ttl(str(resource.get("created_at") or "")):
                continue
            try:
                removed = await self._remove_container(str(resource.get("name") or ""))
                if removed:
                    containers_removed += 1
                else:
                    errors += 1
                    logger.warning("Failed to remove orphaned exec-runner container %s", resource.get("name"))
            except Exception:
                errors += 1
                logger.exception("Failed to remove orphaned exec-runner container %s", resource.get("name"))

        for resource in volumes:
            session_id = str(resource.get("session_id") or self._session_id_from_volume_name(str(resource.get("name") or "")) or "").strip()
            if session_id and session_id in records:
                continue
            if session_id and not self._is_resource_older_than_ttl(str(resource.get("created_at") or "")):
                continue
            try:
                removed = await self._remove_volume(str(resource.get("name") or ""))
                if removed:
                    volumes_removed += 1
                else:
                    errors += 1
                    logger.warning("Failed to remove orphaned exec-runner volume %s", resource.get("name"))
            except Exception:
                errors += 1
                logger.exception("Failed to remove orphaned exec-runner volume %s", resource.get("name"))

        return {
            "containers_removed": containers_removed,
            "volumes_removed": volumes_removed,
            "errors": errors,
        }

    async def _prune_cache_volumes_locked(self) -> dict[str, int]:
        summary = {
            "files_removed": 0,
            "directories_removed": 0,
            "bytes_reclaimed": 0,
            "errors": 0,
        }
        for resource in await self._list_managed_cache_volumes():
            volume_name = str(resource.get("name") or "").strip()
            if not volume_name:
                continue
            try:
                volume_summary = await self._prune_cache_volume(volume_name)
            except Exception:
                summary["errors"] += 1
                logger.exception("Failed to prune exec-runner cache volume %s", volume_name)
                continue
            for key in summary:
                summary[key] += int(volume_summary.get(key, 0) or 0)
        return summary

    async def _prune_cache_volume(self, volume_name: str) -> dict[str, int]:
        stdout = await self._run_docker(
            "run",
            "--rm",
            "--mount",
            f"source={volume_name},target={CACHE_ROOT_IN_CONTAINER}",
            self.config.sandbox_image,
            "python3",
            "-c",
            _CACHE_PRUNE_SCRIPT,
            str(CACHE_ROOT_IN_CONTAINER),
            str(self.config.cache_max_bytes),
            str(self.config.cache_target_bytes),
            str(self.config.cache_max_age_days),
            str(CACHE_RECENT_GUARD_SECONDS),
        )
        try:
            payload = json.loads(stdout or "{}")
        except ValueError as exc:
            raise ExecRunnerError(f"Invalid cache prune response for volume {volume_name}.") from exc
        return {
            "files_removed": int(payload.get("files_removed") or 0),
            "directories_removed": int(payload.get("directories_removed") or 0),
            "bytes_reclaimed": int(payload.get("bytes_reclaimed") or 0),
            "errors": int(payload.get("errors") or 0),
        }

    async def _container_exists(self, container_name: str) -> bool:
        try:
            await self._run_docker("inspect", container_name)
        except ExecRunnerError:
            return False
        return True

    async def _container_has_mount(self, container_name: str, *, source: str, destination: str) -> bool:
        try:
            raw = await self._run_docker("inspect", container_name)
        except ExecRunnerError:
            return False
        try:
            payload = json.loads(raw or "[]")
        except ValueError:
            return False
        if not isinstance(payload, list) or not payload:
            return False
        mounts = payload[0].get("Mounts") or []
        for mount in mounts if isinstance(mounts, list) else []:
            if str(mount.get("Name") or "").strip() != source:
                continue
            if str(mount.get("Destination") or "").strip() != destination:
                continue
            return True
        return False

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

    async def _remove_container(self, container_name: str) -> bool:
        if not container_name:
            return False
        try:
            await self._run_docker("rm", "-f", container_name)
            return True
        except ExecRunnerError:
            return False

    async def _remove_volume(self, volume_name: str) -> bool:
        if not volume_name:
            return False
        try:
            await self._run_docker("volume", "rm", "-f", volume_name)
            return True
        except ExecRunnerError:
            return False

    async def _write_text_into_container(self, container_name: str, path: Path, text: str) -> None:
        await self._write_bytes_into_container(container_name, path, text.encode("utf-8"))

    async def _write_bytes_into_container(self, container_name: str, path: Path, content: bytes) -> None:
        stdout, stderr, status = await self._run_process(
            [
                "docker",
                "exec",
                "-i",
                "-u",
                "nova",
                container_name,
                "python3",
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "target = Path(sys.argv[1]); "
                    "target.parent.mkdir(parents=True, exist_ok=True); "
                    "target.write_bytes(sys.stdin.buffer.read())"
                ),
                str(path),
            ],
            timeout=self.config.command_timeout_seconds,
            input_bytes=content,
        )
        if status != 0:
            raise ExecRunnerError(stderr.strip() or f"Docker exec failed while writing {path}.")

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

    async def _run_process(
        self,
        command: list[str],
        *,
        timeout: int,
        input_bytes: bytes | None = None,
    ) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(input=input_bytes), timeout=timeout)
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
