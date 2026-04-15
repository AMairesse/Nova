from __future__ import annotations

import contextvars
from dataclasses import dataclass
import posixpath
import shlex

from nova.exec_runner import service as exec_runner_service
from nova.runtime.vfs import normalize_vfs_path

_CURRENT_VFS = contextvars.ContextVar("nova_python_current_vfs", default=None)
_CURRENT_WORKDIR = contextvars.ContextVar("nova_python_current_workdir", default=None)


@dataclass(slots=True, frozen=True)
class PythonCommandResult:
    status_description: str
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.status_description == "Accepted"


@dataclass(slots=True, frozen=True)
class PythonWorkspaceFile:
    path: str
    content: bytes
    mime_type: str = "application/octet-stream"


@dataclass(slots=True, frozen=True)
class PythonExecutionRequest:
    code: str = ""
    mode: str = "inline"
    entrypoint: str | None = None
    cwd: str = "."
    workspace_directories: tuple[str, ...] = ()
    workspace_files: tuple[PythonWorkspaceFile, ...] = ()
    timeout: int = 5


@dataclass(slots=True, frozen=True)
class PythonExecutionResult:
    status_description: str
    stdout: str = ""
    stderr: str = ""
    output_files: tuple[PythonWorkspaceFile, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status_description == "Accepted"


async def get_judge0_config(_tool=None) -> dict[str, int]:
    return {"timeout": 5}


def push_runtime_context(vfs, workdir: str | None):
    vfs_token = _CURRENT_VFS.set(vfs)
    workdir_token = _CURRENT_WORKDIR.set(workdir)
    return vfs_token, workdir_token


def pop_runtime_context(tokens) -> None:
    vfs_token, workdir_token = tokens
    _CURRENT_VFS.reset(vfs_token)
    _CURRENT_WORKDIR.reset(workdir_token)


async def execute_python_request(_host: str, request: PythonExecutionRequest) -> PythonExecutionResult:
    current_vfs = _CURRENT_VFS.get()
    current_workdir = _CURRENT_WORKDIR.get()
    if current_vfs is None or not current_workdir:
        raise exec_runner_service.ExecRunnerError(
            "Python execution requires a Nova runtime workspace context."
        )

    cwd_override = normalize_vfs_path(current_workdir, cwd="/")
    if request.mode == "script":
        if not request.entrypoint:
            raise ValueError("Python script execution requires an entrypoint.")
        script_path = normalize_vfs_path(
            posixpath.join(cwd_override, request.entrypoint),
            cwd="/",
        )
        command = f"python {shlex.quote(script_path)}"
    else:
        command = "python -c " + shlex.quote(request.code)

    result, sync_meta = await exec_runner_service.execute_sandbox_shell_command(
        vfs=current_vfs,
        command=command,
        ensure_python=True,
        cwd_override=cwd_override,
    )
    initial_paths = {item.path for item in request.workspace_files}
    output_files: list[PythonWorkspaceFile] = []
    for synced_path in list(sync_meta.get("synced_paths") or []):
        if not synced_path.startswith(f"{cwd_override.rstrip('/')}/") and synced_path != cwd_override:
            continue
        relative_path = posixpath.relpath(synced_path, cwd_override)
        if relative_path in initial_paths:
            continue
        content, mime_type = await current_vfs.read_bytes(synced_path)
        output_files.append(
            PythonWorkspaceFile(
                path=relative_path,
                content=content,
                mime_type=mime_type,
            )
        )
    return PythonExecutionResult(
        status_description="Accepted" if result.status == 0 else f"Exited with status {result.status}",
        stdout=result.stdout,
        stderr=result.stderr,
        output_files=tuple(output_files),
    )


async def test_exec_runner_access(_tool=None) -> dict[str, str]:
    return await exec_runner_service.test_exec_runner_access(_tool)


test_judge0_access = test_exec_runner_access
