from __future__ import annotations

import asyncio
import datetime as dt
import json
import mimetypes
import os
import posixpath
import shutil
import stat
import tempfile
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from nova.memory.service import MEMORY_ROOT
from nova.runtime.vfs import HISTORY_ROOT, INBOX_ROOT, VFSError, VirtualFileSystem, normalize_vfs_path
from nova.webdav.service import WEBDAV_VFS_ROOT


_RUNNER_INTERNAL_DIRNAME = ".nova_runner"
_RUNNER_COMMAND_FILENAME = "command.sh"
_RUNNER_ENV_FILENAME = "env.json"
_RUNNER_CWD_FILENAME = "cwd.txt"
_RUNNER_VENV_DIRNAME = "venv"
_RUNNER_PIP_CACHE_DIRNAME = "pip-cache"
_RUNNER_HOME_DIRNAME = "home"
_READ_ONLY_PROJECTION_ROOTS = ("/skills", INBOX_ROOT, HISTORY_ROOT)
_EXCLUDED_SYNC_ROOTS = {
    "/",
    "/skills",
    INBOX_ROOT,
    HISTORY_ROOT,
    MEMORY_ROOT,
    WEBDAV_VFS_ROOT,
}
_EXCLUDED_SYNC_PREFIXES = tuple(f"{root.rstrip('/')}/" for root in _EXCLUDED_SYNC_ROOTS if root != "/")
_PYTHON_COMMAND_HEADS = {"python", "python3", "pip", "pip3", "uv"}


class ExecRunnerError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class SandboxShellResult:
    stdout: str
    stderr: str
    status: int
    cwd_after: str
    execution_plane: str = "sandbox"


def exec_runner_is_enabled() -> bool:
    return bool(getattr(settings, "EXEC_RUNNER_ENABLED", True))


def _runner_root_dir() -> Path:
    configured = str(getattr(settings, "EXEC_RUNNER_ROOT", "") or "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "nova-exec-runner"


def _runner_ttl_seconds() -> int:
    raw = getattr(settings, "EXEC_RUNNER_TTL_SECONDS", 3600)
    try:
        return max(int(raw), 60)
    except (TypeError, ValueError):
        return 3600


def _runner_shell() -> str:
    configured = str(getattr(settings, "EXEC_RUNNER_SHELL", "") or "").strip()
    if configured:
        return configured
    for candidate in ("/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return "sh"


def _session_workspace_root(vfs: VirtualFileSystem) -> Path:
    user_id = getattr(vfs.user, "id", "anon")
    thread_id = getattr(vfs.thread, "id", "thread")
    agent_id = getattr(vfs.agent_config, "id", "agent")
    return _runner_root_dir() / f"user-{user_id}" / f"thread-{thread_id}" / f"agent-{agent_id}"


def _internal_dir(workspace_root: Path) -> Path:
    return workspace_root / _RUNNER_INTERNAL_DIRNAME


def _command_file(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_COMMAND_FILENAME


def _env_file(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_ENV_FILENAME


def _cwd_file(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_CWD_FILENAME


def _venv_dir(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_VENV_DIRNAME


def _pip_cache_dir(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_PIP_CACHE_DIRNAME


def _home_dir(workspace_root: Path) -> Path:
    return _internal_dir(workspace_root) / _RUNNER_HOME_DIRNAME


def _safe_utc_now() -> dt.datetime:
    return timezone.now().astimezone(dt.timezone.utc)


def _parse_session_timestamp(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _iter_normalized_workspace_files(workspace_root: Path) -> tuple[set[str], set[str]]:
    directories: set[str] = {"/tmp"}
    files: set[str] = set()
    for current_root, dirnames, filenames in os.walk(workspace_root):
        current = Path(current_root)
        relative_root = current.relative_to(workspace_root)
        if relative_root.parts and relative_root.parts[0] == _RUNNER_INTERNAL_DIRNAME:
            dirnames[:] = []
            continue
        normalized_dir = "/" if not relative_root.parts else "/" + "/".join(relative_root.parts)
        if normalized_dir != "/":
            directories.add(normalize_vfs_path(normalized_dir, cwd="/"))
        for dirname in list(dirnames):
            if dirname == _RUNNER_INTERNAL_DIRNAME:
                dirnames.remove(dirname)
        for filename in filenames:
            relative_path = relative_root / filename if relative_root.parts else Path(filename)
            normalized = normalize_vfs_path("/" + relative_path.as_posix(), cwd="/")
            if normalized.startswith(f"/{_RUNNER_INTERNAL_DIRNAME}/"):
                continue
            files.add(normalized)
    return directories, files


def _workspace_path_for_vfs_path(workspace_root: Path, vfs_path: str) -> Path:
    normalized = normalize_vfs_path(vfs_path, cwd="/")
    if normalized == "/":
        return workspace_root
    return workspace_root / normalized.lstrip("/")


def _vfs_path_for_workspace_path(workspace_root: Path, raw_path: str) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return "/"
    try:
        relative = Path(text).resolve().relative_to(workspace_root.resolve())
    except Exception:
        return normalize_vfs_path(text, cwd="/")
    if not relative.parts:
        return "/"
    return normalize_vfs_path("/" + relative.as_posix(), cwd="/")


def _set_path_read_only(path: Path) -> None:
    if path.is_dir():
        path.chmod(0o555)
        return
    path.chmod(0o444)


def _set_tree_writable(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file():
        path.chmod(0o644)
        return
    for current_root, dirnames, filenames in os.walk(path):
        root_path = Path(current_root)
        root_path.chmod(0o755)
        for dirname in dirnames:
            (root_path / dirname).chmod(0o755)
        for filename in filenames:
            (root_path / filename).chmod(0o644)


def _purge_tree(path: Path) -> None:
    if not path.exists():
        return
    _set_tree_writable(path)
    shutil.rmtree(path)


async def _gc_expired_workspace(vfs: VirtualFileSystem) -> None:
    session_state = dict(vfs.session_state or {})
    last_used = _parse_session_timestamp(session_state.get("sandbox_last_used_at"))
    if last_used is None:
        return
    delta = _safe_utc_now() - last_used.astimezone(dt.timezone.utc)
    if delta.total_seconds() <= _runner_ttl_seconds():
        return
    workspace_root = _session_workspace_root(vfs)
    if workspace_root.exists():
        await asyncio.to_thread(_purge_tree, workspace_root)
    session_state.pop("sandbox_env", None)
    session_state.pop("sandbox_last_used_at", None)
    vfs.session_state.update(session_state)


def _base_environment(workspace_root: Path) -> dict[str, str]:
    home_dir = _home_dir(workspace_root)
    venv_dir = _venv_dir(workspace_root)
    pip_cache_dir = _pip_cache_dir(workspace_root)
    path_parts = []
    venv_bin = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    if venv_bin.exists():
        path_parts.append(str(venv_bin))
    system_path = str(os.environ.get("PATH") or "").strip()
    if system_path:
        path_parts.append(system_path)
    env = {
        "HOME": str(home_dir),
        "PATH": os.pathsep.join(path_parts) if path_parts else system_path,
        "LANG": str(os.environ.get("LANG") or "C.UTF-8"),
        "LC_ALL": str(os.environ.get("LC_ALL") or os.environ.get("LANG") or "C.UTF-8"),
        "PIP_CACHE_DIR": str(pip_cache_dir),
        "PYTHONUNBUFFERED": "1",
    }
    if venv_bin.exists():
        env["VIRTUAL_ENV"] = str(venv_dir)
    return env


async def _ensure_workspace_root(vfs: VirtualFileSystem) -> Path:
    await _gc_expired_workspace(vfs)
    workspace_root = _session_workspace_root(vfs)
    internal_dir = _internal_dir(workspace_root)
    home_dir = _home_dir(workspace_root)
    pip_cache_dir = _pip_cache_dir(workspace_root)
    await asyncio.to_thread(workspace_root.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(internal_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(home_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(pip_cache_dir.mkdir, parents=True, exist_ok=True)
    return workspace_root


async def _ensure_python_virtualenv(workspace_root: Path) -> None:
    venv_dir = _venv_dir(workspace_root)
    python_binary = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    if python_binary.exists():
        return
    command = [os.environ.get("PYTHON", "python3"), "-m", "venv", str(venv_dir)]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise ExecRunnerError(
            _("Failed to initialize the Python sandbox environment: %(error)s")
            % {"error": (stderr or b"").decode("utf-8", errors="replace").strip() or "venv failed"}
        )


async def _materialize_regular_workspace(vfs: VirtualFileSystem, workspace_root: Path) -> None:
    desired_dirs: set[str] = {"/tmp"}
    desired_files: dict[str, tuple[bytes, str]] = {}
    for path in sorted(set(await vfs.find("/", ""))):
        normalized = normalize_vfs_path(path, cwd="/")
        if normalized in _EXCLUDED_SYNC_ROOTS or normalized.startswith(_EXCLUDED_SYNC_PREFIXES):
            continue
        if await vfs.is_dir(normalized):
            desired_dirs.add(normalized)
            continue
        content, mime_type = await vfs.read_bytes(normalized)
        desired_files[normalized] = (content, mime_type)
        parent = posixpath.dirname(normalized)
        while parent and parent != "/":
            desired_dirs.add(parent)
            parent = posixpath.dirname(parent)

    current_dirs, current_files = await asyncio.to_thread(_iter_normalized_workspace_files, workspace_root)
    internal_dir = _internal_dir(workspace_root)
    if internal_dir.exists():
        current_dirs.add(f"/{_RUNNER_INTERNAL_DIRNAME}")

    files_to_remove = sorted(current_files - set(desired_files.keys()))
    for removed in files_to_remove:
        if removed in {INBOX_ROOT, HISTORY_ROOT, "/skills"} or removed.startswith("/skills/") or removed.startswith(f"{INBOX_ROOT}/") or removed.startswith(f"{HISTORY_ROOT}/"):
            continue
        target = _workspace_path_for_vfs_path(workspace_root, removed)
        await asyncio.to_thread(_set_tree_writable, target)
        if target.exists():
            await asyncio.to_thread(target.unlink)

    dirs_to_remove = sorted(
        directory
        for directory in current_dirs - desired_dirs
        if directory not in {"/", "/tmp", f"/{_RUNNER_INTERNAL_DIRNAME}"}
        and directory not in _READ_ONLY_PROJECTION_ROOTS
    )
    for removed_dir in sorted(dirs_to_remove, key=len, reverse=True):
        target = _workspace_path_for_vfs_path(workspace_root, removed_dir)
        if target.exists():
            await asyncio.to_thread(_purge_tree, target)

    for directory in sorted(desired_dirs):
        target = _workspace_path_for_vfs_path(workspace_root, directory)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)

    for normalized, (content, _mime_type) in desired_files.items():
        target = _workspace_path_for_vfs_path(workspace_root, normalized)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        existing = b""
        if target.exists():
            existing = await asyncio.to_thread(target.read_bytes)
        if existing != content:
            await asyncio.to_thread(target.write_bytes, content)


async def _materialize_projection(vfs: VirtualFileSystem, workspace_root: Path, root_path: str) -> None:
    target_root = _workspace_path_for_vfs_path(workspace_root, root_path)
    if target_root.exists():
        await asyncio.to_thread(_purge_tree, target_root)
    await asyncio.to_thread(target_root.mkdir, parents=True, exist_ok=True)
    if not await vfs.path_exists(root_path):
        await asyncio.to_thread(_set_path_read_only, target_root)
        return

    matches = sorted(set(await vfs.find(root_path, "")))
    for matched in matches:
        normalized = normalize_vfs_path(matched, cwd="/")
        if normalized == root_path:
            continue
        relative = posixpath.relpath(normalized, root_path)
        if relative.startswith("../"):
            continue
        destination = target_root / relative
        if await vfs.is_dir(normalized):
            await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
            continue
        if normalized.startswith("/skills/"):
            content = (await vfs.read_text(normalized)).encode("utf-8")
        else:
            content, _mime_type = await vfs.read_bytes(normalized)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, content)
        await asyncio.to_thread(_set_path_read_only, destination)
    for current_root, dirnames, filenames in os.walk(target_root):
        root = Path(current_root)
        for dirname in dirnames:
            _set_path_read_only(root / dirname)
        for filename in filenames:
            _set_path_read_only(root / filename)
    await asyncio.to_thread(_set_path_read_only, target_root)


async def _prepare_sandbox_workspace(vfs: VirtualFileSystem, workspace_root: Path) -> None:
    await _materialize_regular_workspace(vfs, workspace_root)
    for root_path in _READ_ONLY_PROJECTION_ROOTS:
        await _materialize_projection(vfs, workspace_root, root_path)


async def _sync_workspace_back_to_vfs(vfs: VirtualFileSystem, workspace_root: Path) -> dict[str, list[str]]:
    before_files = await vfs.snapshot_visible_files(include_inbox=False)
    workspace_dirs, workspace_files = await asyncio.to_thread(_iter_normalized_workspace_files, workspace_root)
    workspace_dirs = {
        directory
        for directory in workspace_dirs
        if directory not in {"/", "/skills", INBOX_ROOT, HISTORY_ROOT, MEMORY_ROOT, WEBDAV_VFS_ROOT}
        and not directory.startswith("/skills/")
        and not directory.startswith(f"{INBOX_ROOT}/")
        and not directory.startswith(f"{HISTORY_ROOT}/")
    }
    workspace_files = {
        path
        for path in workspace_files
        if path not in {"/skills", INBOX_ROOT, HISTORY_ROOT, MEMORY_ROOT, WEBDAV_VFS_ROOT}
        and not path.startswith("/skills/")
        and not path.startswith(f"{INBOX_ROOT}/")
        and not path.startswith(f"{HISTORY_ROOT}/")
    }

    synced: list[str] = []
    removed: list[str] = []
    for path in sorted(workspace_files):
        source = _workspace_path_for_vfs_path(workspace_root, path)
        content = await asyncio.to_thread(source.read_bytes)
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        existing = None
        if path in before_files:
            try:
                existing, existing_mime = await vfs.read_bytes(path)
            except VFSError:
                existing = None
                existing_mime = ""
            if existing == content and existing_mime == mime_type:
                continue
        await vfs.write_file(path, content, mime_type=mime_type, overwrite=True)
        synced.append(path)

    removed_candidates = sorted(path for path in before_files if path not in workspace_files)
    for path in removed_candidates:
        if path == "/tmp" or path.startswith(f"{INBOX_ROOT}/") or path.startswith(f"{HISTORY_ROOT}/"):
            continue
        try:
            await vfs.remove(path, recursive=False)
        except VFSError:
            continue
        removed.append(path)

    reserved_dirs = {
        directory
        for directory in list(vfs.session_state.get("directories") or [])
        if normalize_vfs_path(directory, cwd="/").startswith("/tmp")
    }
    vfs.session_state["directories"] = sorted(
        {normalize_vfs_path(path, cwd="/") for path in workspace_dirs}.union(reserved_dirs)
    )
    return {
        "synced_paths": synced,
        "removed_paths": removed,
    }


def _encode_environment_script(env: dict[str, str]) -> str:
    lines = ["set +e"]
    for key in sorted(env.keys()):
        value = str(env[key])
        safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'export {key}="{safe_value}"')
    return "\n".join(lines)


def _load_persisted_env(workspace_root: Path) -> dict[str, str]:
    env_path = _env_file(workspace_root)
    if not env_path.exists():
        return {}
    try:
        data = json.loads(env_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _rewrite_token_for_workspace(token: str, workspace_root: Path) -> str:
    value = str(token or "")
    if not value or value in {"|", ";", "&&", "||", ">", ">>", "<"}:
        return value
    if "://" in value:
        return value
    if value == "/":
        return str(workspace_root)
    if not value.startswith("/"):
        return value
    return str(_workspace_path_for_vfs_path(workspace_root, value))


def _rewrite_shell_command_for_workspace(command: str, workspace_root: Path) -> str:
    raw = str(command or "")
    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return raw
    rewritten = [_rewrite_token_for_workspace(token, workspace_root) for token in tokens]
    rendered_tokens: list[str] = []
    operator_tokens = {"|", ";", "&&", "||", ">", ">>", "<"}
    for token in rewritten:
        if token in operator_tokens:
            rendered_tokens.append(token)
        else:
            has_shell_substitution = "$(" in token or "`" in token
            needs_quotes = (
                not token
                or any(character.isspace() for character in token)
                or (
                    any(character in token for character in "\"'<>;&|()")
                    and not has_shell_substitution
                )
            )
            rendered_tokens.append(shlex.quote(token) if needs_quotes else token)
    return " ".join(rendered_tokens)


def _rewrite_output_paths_from_workspace(text: str, workspace_root: Path) -> str:
    rendered = str(text or "")
    root = str(workspace_root)
    if not root:
        return rendered
    candidates = {
        root: "/",
        f"{root}/tmp": "/tmp",
        f"{root}/skills": "/skills",
        f"{root}/inbox": INBOX_ROOT,
        f"{root}/history": HISTORY_ROOT,
    }
    for candidate, replacement in sorted(candidates.items(), key=lambda item: len(item[0]), reverse=True):
        rendered = rendered.replace(candidate, replacement)
    return rendered


async def _run_shell_command(
    *,
    workspace_root: Path,
    command: str,
    cwd: str,
    env: dict[str, str],
) -> SandboxShellResult:
    internal_dir = _internal_dir(workspace_root)
    command_path = _command_file(workspace_root)
    cwd_path = _cwd_file(workspace_root)
    env_path = _env_file(workspace_root)
    rewritten_command = _rewrite_shell_command_for_workspace(command, workspace_root)
    await asyncio.to_thread(command_path.write_text, rewritten_command, "utf-8")

    actual_cwd = str(_workspace_path_for_vfs_path(workspace_root, cwd))

    shell = _runner_shell()
    command_script = "\n".join(
        [
            "set +e",
            f'cd "{actual_cwd}" || exit 1',
            _encode_environment_script(env),
            f'. "{command_path}"',
            "status=$?",
            'pwd > "$NOVA_CWD_FILE"',
            'python3 - <<\'PYENV\' > "$NOVA_ENV_FILE"',
            "import json, os",
            "print(json.dumps(dict(os.environ), ensure_ascii=False))",
            "PYENV",
            "exit $status",
        ]
    )
    process = await asyncio.create_subprocess_exec(
        shell,
        "-lc",
        command_script,
        cwd=str(workspace_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "NOVA_CWD_FILE": str(cwd_path),
            "NOVA_ENV_FILE": str(env_path),
        },
    )
    stdout, stderr = await process.communicate()
    try:
        persisted_cwd = await asyncio.to_thread(cwd_path.read_text, "utf-8")
        cwd_after = _vfs_path_for_workspace_path(workspace_root, str(persisted_cwd).strip())
    except OSError:
        cwd_after = normalize_vfs_path(cwd, cwd="/")
    return SandboxShellResult(
        stdout=_rewrite_output_paths_from_workspace(
            (stdout or b"").decode("utf-8", errors="replace"),
            workspace_root,
        ),
        stderr=_rewrite_output_paths_from_workspace(
            (stderr or b"").decode("utf-8", errors="replace"),
            workspace_root,
        ),
        status=int(process.returncode or 0),
        cwd_after=cwd_after,
    )


async def execute_sandbox_shell_command(
    *,
    vfs: VirtualFileSystem,
    command: str,
    ensure_python: bool = False,
    cwd_override: str | None = None,
) -> tuple[SandboxShellResult, dict[str, list[str]]]:
    if not exec_runner_is_enabled():
        raise ExecRunnerError("The Nova exec runner is not enabled.")

    workspace_root = await _ensure_workspace_root(vfs)
    await _prepare_sandbox_workspace(vfs, workspace_root)
    if ensure_python:
        await _ensure_python_virtualenv(workspace_root)

    persisted_env = _load_persisted_env(workspace_root)
    env = _base_environment(workspace_root)
    env.update({key: value for key, value in persisted_env.items() if not key.startswith("NOVA_")})
    cwd = normalize_vfs_path(str(cwd_override or vfs.session_state.get("cwd") or "/"), cwd="/")
    if cwd in {"/skills", INBOX_ROOT, HISTORY_ROOT, MEMORY_ROOT, WEBDAV_VFS_ROOT}:
        cwd = "/"

    result = await _run_shell_command(
        workspace_root=workspace_root,
        command=command,
        cwd=cwd,
        env=env,
    )
    sync_meta = await _sync_workspace_back_to_vfs(vfs, workspace_root)
    vfs.set_cwd(result.cwd_after)
    vfs.session_state["sandbox_last_used_at"] = _safe_utc_now().isoformat()
    return result, sync_meta


async def execute_sandbox_python_command(
    *,
    vfs: VirtualFileSystem,
    args: list[str],
    cwd_override: str | None = None,
) -> tuple[SandboxShellResult, dict[str, list[str]]]:
    command = "python " + " ".join(args)
    return await execute_sandbox_shell_command(
        vfs=vfs,
        command=command,
        ensure_python=True,
        cwd_override=cwd_override,
    )


async def test_exec_runner_access(_tool=None) -> dict[str, str]:
    if not exec_runner_is_enabled():
        return {
            "status": "error",
            "message": _("The Nova exec runner is disabled."),
        }
    return {
        "status": "success",
        "message": _("Nova sandbox terminal is available."),
    }
