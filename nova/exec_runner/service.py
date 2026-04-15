from __future__ import annotations

import io
import json
import mimetypes
import tarfile
import tempfile
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path

import httpx
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from nova.memory.service import MEMORY_ROOT
from nova.runtime.vfs import HISTORY_ROOT, INBOX_ROOT, VFSError, VirtualFileSystem, normalize_vfs_path
from nova.webdav.service import WEBDAV_VFS_ROOT

from .shared import (
    EXCLUDED_SYNC_PREFIXES,
    EXCLUDED_SYNC_ROOTS,
    ExecRunnerError,
    ExecSessionSelector,
    SandboxShellResult,
)


def exec_runner_is_enabled() -> bool:
    return bool(getattr(settings, "EXEC_RUNNER_ENABLED", False))


def _runner_base_url() -> str:
    return str(getattr(settings, "EXEC_RUNNER_BASE_URL", "") or "").strip()


def _runner_shared_token() -> str:
    return str(getattr(settings, "EXEC_RUNNER_SHARED_TOKEN", "") or "").strip()


def exec_runner_is_configured() -> bool:
    return bool(
        exec_runner_is_enabled()
        and _runner_base_url()
        and _runner_shared_token()
    )


def _runner_timeout_seconds() -> float:
    raw = getattr(settings, "EXEC_RUNNER_REQUEST_TIMEOUT_SECONDS", 120)
    try:
        return max(float(raw), 5.0)
    except (TypeError, ValueError):
        return 120.0


def _runner_headers() -> dict[str, str]:
    token = _runner_shared_token()
    if not token:
        raise ExecRunnerError("The Nova exec runner shared token is not configured.")
    return {"Authorization": f"Bearer {token}"}


def _selector_for_vfs(vfs: VirtualFileSystem) -> ExecSessionSelector:
    return ExecSessionSelector(
        user_id=getattr(vfs.user, "id", "anon"),
        thread_id=getattr(vfs.thread, "id", "thread"),
        agent_id=getattr(vfs.agent_config, "id", "agent"),
    )


def _add_directory_entry(archive: tarfile.TarFile, arcname: str) -> None:
    normalized = str(arcname or "").strip().strip("/")
    if not normalized:
        return
    info = tarfile.TarInfo(name=normalized)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    archive.addfile(info)


async def _add_file_entry(
    archive: tarfile.TarFile,
    *,
    vfs: VirtualFileSystem,
    path: str,
) -> None:
    content, _mime_type = await vfs.read_bytes(path)
    info = tarfile.TarInfo(name=path.lstrip("/"))
    info.size = len(content)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(content))


async def _build_sync_bundle(vfs: VirtualFileSystem) -> bytes:
    with tempfile.NamedTemporaryFile(prefix="nova-sync-", suffix=".tar.gz", delete=False) as handle:
        bundle_path = Path(handle.name)
    try:
        with tarfile.open(bundle_path, "w:gz") as archive:
            desired_dirs: set[str] = {"/tmp", INBOX_ROOT, HISTORY_ROOT, "/skills"}
            desired_files: set[str] = set()

            for path in sorted(set(await vfs.find("/", ""))):
                normalized = normalize_vfs_path(path, cwd="/")
                if normalized in {MEMORY_ROOT, WEBDAV_VFS_ROOT}:
                    continue
                if normalized in EXCLUDED_SYNC_ROOTS or normalized.startswith(EXCLUDED_SYNC_PREFIXES):
                    continue
                if await vfs.is_dir(normalized):
                    if normalized != "/":
                        desired_dirs.add(normalized)
                    continue
                desired_files.add(normalized)
                parent = normalized.rsplit("/", 1)[0] or "/"
                while parent and parent != "/":
                    desired_dirs.add(parent)
                    parent = parent.rsplit("/", 1)[0] or "/"

            for root_path in ("/skills", INBOX_ROOT, HISTORY_ROOT):
                if await vfs.path_exists(root_path):
                    if await vfs.is_dir(root_path):
                        desired_dirs.add(root_path)
                    for path in sorted(set(await vfs.find(root_path, ""))):
                        normalized = normalize_vfs_path(path, cwd="/")
                        if normalized == root_path:
                            if await vfs.is_dir(normalized):
                                desired_dirs.add(normalized)
                            continue
                        if await vfs.is_dir(normalized):
                            desired_dirs.add(normalized)
                            continue
                        desired_files.add(normalized)
                        parent = normalized.rsplit("/", 1)[0] or "/"
                        while parent and parent != "/":
                            desired_dirs.add(parent)
                            if parent == root_path:
                                break
                            parent = parent.rsplit("/", 1)[0] or "/"

            for directory in sorted(desired_dirs):
                _add_directory_entry(archive, directory)
            for path in sorted(desired_files):
                await _add_file_entry(archive, vfs=vfs, path=path)

        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def _parse_multipart_response(response: httpx.Response) -> tuple[dict, bytes]:
    content_type = str(response.headers.get("content-type") or "").strip()
    if "multipart/form-data" not in content_type:
        raise ExecRunnerError("Invalid exec runner response format.")
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + response.content
    )
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    metadata: dict = {}
    diff_bundle = b""
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if 'name="metadata"' in disposition:
            metadata = json.loads(part.get_content())
        elif 'name="diff_bundle"' in disposition:
            payload = part.get_payload(decode=True)
            diff_bundle = payload or b""
    return metadata, diff_bundle


async def _apply_diff_bundle(
    vfs: VirtualFileSystem,
    *,
    diff_bundle_bytes: bytes,
    removed_paths: list[str],
    directory_paths: list[str],
) -> dict[str, list[str]]:
    synced_paths: list[str] = []
    if diff_bundle_bytes:
        with tarfile.open(fileobj=io.BytesIO(diff_bundle_bytes), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                path = normalize_vfs_path("/" + member.name.lstrip("/"), cwd="/")
                content = extracted.read()
                mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                await vfs.write_file(path, content, mime_type=mime_type, overwrite=True)
                synced_paths.append(path)

    removed: list[str] = []
    for path in list(removed_paths or []):
        normalized = normalize_vfs_path(path, cwd="/")
        try:
            await vfs.remove(normalized, recursive=False)
        except VFSError:
            continue
        removed.append(normalized)

    vfs.session_state["directories"] = sorted(
        {
            normalize_vfs_path(path, cwd="/")
            for path in list(directory_paths or [])
            if str(path or "").strip()
        }
    )
    return {
        "synced_paths": synced_paths,
        "removed_paths": removed,
    }


async def execute_sandbox_shell_command(
    *,
    vfs: VirtualFileSystem,
    command: str,
    ensure_python: bool = False,
    cwd_override: str | None = None,
) -> tuple[SandboxShellResult, dict[str, list[str]]]:
    if not exec_runner_is_enabled():
        raise ExecRunnerError("The Nova exec runner is disabled.")
    if not exec_runner_is_configured():
        raise ExecRunnerError("The Nova exec runner is not fully configured.")

    base_url = _runner_base_url().rstrip("/")

    cwd = normalize_vfs_path(str(cwd_override or vfs.session_state.get("cwd") or "/"), cwd="/")
    if cwd in {"/skills", INBOX_ROOT, HISTORY_ROOT, MEMORY_ROOT, WEBDAV_VFS_ROOT}:
        cwd = "/"

    sync_bundle_bytes = await _build_sync_bundle(vfs)
    metadata = {
        "selector": {
            "user_id": getattr(vfs.user, "id", "anon"),
            "thread_id": getattr(vfs.thread, "id", "thread"),
            "agent_id": getattr(vfs.agent_config, "id", "agent"),
        },
        "command": str(command or ""),
        "cwd": cwd,
        "ensure_python": bool(ensure_python),
    }

    with tempfile.NamedTemporaryFile(prefix="nova-sync-request-", suffix=".tar.gz", delete=False) as handle:
        request_bundle_path = Path(handle.name)
        handle.write(sync_bundle_bytes)
    try:
        async with httpx.AsyncClient(timeout=_runner_timeout_seconds()) as client:
            with request_bundle_path.open("rb") as bundle_handle:
                response = await client.post(
                    f"{base_url}/v1/sessions/exec",
                    headers=_runner_headers(),
                    data={"metadata": json.dumps(metadata, ensure_ascii=False)},
                    files={"sync_bundle": ("sync.tar.gz", bundle_handle, "application/gzip")},
                )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = str(payload.get("message") or f"Exec runner request failed with status {response.status_code}.")
            raise ExecRunnerError(message)
        response_metadata, diff_bundle_bytes = _parse_multipart_response(response)
        sync_meta = await _apply_diff_bundle(
            vfs,
            diff_bundle_bytes=diff_bundle_bytes,
            removed_paths=list(response_metadata.get("removed_paths") or []),
            directory_paths=list(response_metadata.get("directory_paths") or []),
        )
        result = SandboxShellResult(
            stdout=str(response_metadata.get("stdout") or ""),
            stderr=str(response_metadata.get("stderr") or ""),
            status=int(response_metadata.get("status") or 0),
            cwd_after=normalize_vfs_path(str(response_metadata.get("cwd_after") or "/"), cwd="/"),
            execution_plane=str(response_metadata.get("execution_plane") or "sandbox"),
        )
        vfs.set_cwd(result.cwd_after)
        return result, sync_meta
    except httpx.HTTPError as exc:
        raise ExecRunnerError(f"Could not reach the Nova exec runner: {exc}") from exc
    finally:
        request_bundle_path.unlink(missing_ok=True)


async def execute_sandbox_python_command(
    *,
    vfs: VirtualFileSystem,
    args: list[str],
    cwd_override: str | None = None,
) -> tuple[SandboxShellResult, dict[str, list[str]]]:
    import shlex

    command = "python " + " ".join(shlex.quote(str(arg or "")) for arg in args)
    return await execute_sandbox_shell_command(
        vfs=vfs,
        command=command,
        ensure_python=True,
        cwd_override=cwd_override,
    )


async def delete_sandbox_session(vfs: VirtualFileSystem) -> None:
    if not exec_runner_is_configured():
        return
    base_url = _runner_base_url().rstrip("/")
    if not base_url:
        return
    selector = _selector_for_vfs(vfs)
    try:
        async with httpx.AsyncClient(timeout=_runner_timeout_seconds()) as client:
            await client.delete(
                f"{base_url}/v1/sessions/{selector.session_id}",
                headers=_runner_headers(),
            )
    except httpx.HTTPError as exc:
        raise ExecRunnerError(f"Could not reach the Nova exec runner: {exc}") from exc


async def test_exec_runner_access(_tool=None) -> dict[str, str]:
    if not exec_runner_is_enabled():
        return {
            "status": "error",
            "message": _("The Nova exec runner is disabled."),
        }
    if not exec_runner_is_configured():
        return {
            "status": "error",
            "message": _("The Nova exec runner is not configured."),
        }
    base_url = _runner_base_url().rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_runner_timeout_seconds()) as client:
            response = await client.get(
                f"{base_url}/healthz",
                headers=_runner_headers(),
            )
        if response.status_code >= 400:
            message = response.json().get("message", "Exec runner unavailable.")
            return {
                "status": "error",
                "message": str(message),
            }
    except (httpx.HTTPError, ExecRunnerError) as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    return {
        "status": "success",
        "message": _("Nova sandbox terminal is available through exec-runner."),
    }
