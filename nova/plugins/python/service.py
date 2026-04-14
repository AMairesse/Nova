# nova/plugins/python/service.py
from __future__ import annotations

import aiohttp
import asyncio
import base64
import json
import logging
import mimetypes
import posixpath
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool, ToolCredential

logger = logging.getLogger(__name__)

# Cache for languages to avoid repeated API calls
_languages_cache: Optional[List[Dict[str, Union[int, str]]]] = None

PYTHON_WORKSPACE_MAX_FILES = 128
PYTHON_WORKSPACE_MAX_BYTES = 1024 * 1024
PYTHON_WRITEBACK_MAX_FILES = 128
PYTHON_WRITEBACK_MAX_BYTES = 1024 * 1024
PYTHON_WORKSPACE_RESULT_MARKER = "__NOVA_PYTHON_WORKSPACE_RESULT__:"

_JUDGE0_PYTHON_WORKSPACE_WRAPPER = textwrap.dedent(
    f"""
    import base64
    import contextlib
    import hashlib
    import io
    import json
    import mimetypes
    import os
    import posixpath
    import runpy
    import sys
    import tempfile
    import traceback

    RESULT_MARKER = {PYTHON_WORKSPACE_RESULT_MARKER!r}

    def _safe_relpath(value, *, allow_dot=False):
        raw = str(value or "").strip().replace("\\\\", "/")
        normalized = posixpath.normpath(raw or ".")
        if normalized in {{"", "."}}:
            if allow_dot:
                return "."
            raise ValueError("Empty workspace path.")
        if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"Invalid workspace path: {{value}}")
        return normalized

    payload = json.loads(sys.stdin.read() or "{{}}")
    workspace_root = tempfile.mkdtemp(prefix="nova-python-")

    initial_hashes = {{}}
    initial_mime_types = {{}}
    for directory in sorted(set(payload.get("directories") or [])):
        rel_dir = _safe_relpath(directory, allow_dot=True)
        if rel_dir == ".":
            continue
        os.makedirs(os.path.join(workspace_root, *rel_dir.split("/")), exist_ok=True)

    for item in payload.get("files") or []:
        rel_path = _safe_relpath(item.get("path"), allow_dot=False)
        abs_path = os.path.join(workspace_root, *rel_path.split("/"))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        data = base64.b64decode(item.get("content_b64") or "")
        with open(abs_path, "wb") as handle:
            handle.write(data)
        initial_hashes[rel_path] = hashlib.sha256(data).hexdigest()
        initial_mime_types[rel_path] = str(item.get("mime_type") or "application/octet-stream")

    cwd_rel = _safe_relpath(payload.get("cwd", "."), allow_dot=True)
    cwd_abs = workspace_root if cwd_rel == "." else os.path.join(workspace_root, *cwd_rel.split("/"))
    os.makedirs(cwd_abs, exist_ok=True)
    os.chdir(cwd_abs)
    if cwd_abs not in sys.path:
        sys.path.insert(0, cwd_abs)

    user_status = "Accepted"
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    original_argv = list(sys.argv)
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            mode = str(payload.get("mode") or "inline").strip().lower()
            if mode == "script":
                entry_rel = _safe_relpath(payload.get("entrypoint"), allow_dot=False)
                entry_abs = os.path.join(workspace_root, *entry_rel.split("/"))
                entry_dir = os.path.dirname(entry_abs)
                if entry_dir and entry_dir not in sys.path:
                    sys.path.insert(0, entry_dir)
                sys.argv = [entry_abs]
                runpy.run_path(entry_abs, run_name="__main__")
            else:
                sys.argv = ["-c"]
                code = str(payload.get("code") or "")
                globals_dict = {{"__name__": "__main__"}}
                exec(compile(code, "<nova-python>", "exec"), globals_dict, globals_dict)
    except SystemExit as exc:
        exit_code = exc.code
        if isinstance(exit_code, int):
            if exit_code != 0:
                user_status = f"Exited with status {{exit_code}}"
        elif exit_code:
            user_status = "Runtime Error"
            stderr_buffer.write(str(exit_code))
    except Exception:
        user_status = "Runtime Error"
        stderr_buffer.write(traceback.format_exc())
    finally:
        sys.argv = original_argv

    output_files = []
    for current_root, _dirnames, filenames in os.walk(workspace_root):
        for filename in filenames:
            abs_path = os.path.join(current_root, filename)
            rel_path = posixpath.relpath(abs_path, workspace_root).replace(os.sep, "/")
            with open(abs_path, "rb") as handle:
                content = handle.read()
            digest = hashlib.sha256(content).hexdigest()
            if initial_hashes.get(rel_path) == digest:
                continue
            output_files.append(
                {{
                    "path": rel_path,
                    "content_b64": base64.b64encode(content).decode("utf-8"),
                    "mime_type": (
                        initial_mime_types.get(rel_path)
                        or mimetypes.guess_type(rel_path)[0]
                        or "application/octet-stream"
                    ),
                }}
            )

    envelope = {{
        "status": user_status,
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "files": output_files,
    }}
    print(RESULT_MARKER + base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("utf-8"))
    """
).strip()


@dataclass(slots=True, frozen=True)
class Judge0ExecutionResult:
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


async def test_judge0_access(tool: Tool) -> Dict[str, str]:
    """Test connection to Judge0 by fetching supported languages."""
    host = await get_judge0_host(tool)
    languages = await fetch_languages(host)
    count = len(languages)
    if count > 0:
        return {
            "status": "success",
            "message": _(f"{count} supported languages found")
        }
    else:
        return {
            "status": "error",
            "message": _("No languages found - check server configuration")
        }


async def get_judge0_host(tool: Tool) -> str:
    """Fetch Judge0 URL from tool credentials."""
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    cred = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False
    )()
    if not cred:
        raise ValueError(_("No credential configured for this Code Execution tool."))

    host = cred.config.get("judge0_url")
    if not host:
        raise ValueError(_("Field ‘judge0_url’ is missing from the configuration."))

    return host.rstrip("/")


async def get_judge0_config(tool: Tool) -> Dict[str, any]:
    """Fetch full Judge0 config from credentials."""
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    cred = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False
    )()
    if not cred:
        raise ValueError(_("No credential configured for this Code Execution tool."))

    return {
        "url": cred.config.get("judge0_url", "").rstrip("/"),
        "api_key": cred.config.get("api_key"),
        "timeout": int(cred.config.get("timeout", 5)) if cred.config.get("timeout") else 5,
    }


async def api_request(
    method: str,
    url: str,
    headers: Optional[Dict] = None,
    data: Optional[Dict] = None,
    api_key=None,
) -> Dict:
    """Async HTTP request helper for Judge0 API."""
    hdrs = dict(headers or {})
    if api_key:
        hdrs["X-Auth-Token"] = api_key
    async with aiohttp.ClientSession() as session:
        kwargs = {"headers": hdrs, "json": data} if data else {"headers": hdrs}
        async with session.request(method, url, **kwargs) as response:
            if response.status != 200 and response.status != 201:
                error_text = await response.text()
                raise ValueError(f"Judge0 API error: {response.status} - {error_text}")
            return await response.json()


async def fetch_languages(host: str) -> List[Dict[str, Union[int, str]]]:
    """Fetch and cache supported languages from Judge0."""
    global _languages_cache
    if _languages_cache is None:
        try:
            response = await api_request("GET", f"{host}/languages")
            _languages_cache = response
        except Exception as exc:
            logger.error("Error fetching languages: %s", str(exc))
            _languages_cache = []
    return _languages_cache


async def list_supported_languages(host: str) -> str:
    """List supported languages with IDs and names."""
    languages = await fetch_languages(host)
    if not languages:
        return _("No languages available.")
    formatted = [f"{lang['id']}: {lang['name']}" for lang in languages]
    return ", ".join(formatted)


async def get_language_id(host: str, language: Union[str, int]) -> int:
    """Map language name or ID to Judge0 language_id. Default to Python if not specified."""
    if isinstance(language, int):
        return language

    if not language:
        language = "python"

    languages = await fetch_languages(host)
    if not languages:
        raise ValueError(_("No languages available. Please check server configuration."))

    language_lower = language.lower()
    matches = [lang["id"] for lang in languages if language_lower in lang["name"].lower()]

    if not matches:
        raise ValueError(
            _("Language '{lang}' not found.\
            Use list_supported_languages to see available options.").format(lang=language)
        )

    return max(matches)


async def get_execution_status(host: str, token: str) -> str:
    """Get status of a submission."""
    result = await get_execution_status_result(host, token)
    return format_execution_result(result)


async def get_execution_status_result(host: str, token: str) -> Judge0ExecutionResult:
    """Get structured status of a submission."""
    response = await api_request("GET", f"{host}/submissions/{token}?base64_encoded=true")
    status = response.get("status", {}).get("description", "Unknown")
    stdout = response.get("stdout", "")
    decoded_stdout = base64.b64decode(stdout).decode("utf-8") if stdout else ""
    stderr = response.get("stderr", "")
    decoded_stderr = base64.b64decode(stderr).decode("utf-8") if stderr else ""
    return Judge0ExecutionResult(
        status_description=status,
        stdout=decoded_stdout,
        stderr=decoded_stderr,
    )


async def compile_code(host: str, code: str, language: Union[str, int]) -> str:
    """Compile code without execution (for compiled languages)."""
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    data = {
        "source_code": encoded_code,
        "language_id": language_id,
        "compiler_options": "",
        "command_line_arguments": "",
    }
    response = await api_request("POST", f"{host}/submissions?base64_encoded=true&wait=false", data=data)
    token = response.get("token")

    for _i in range(10):
        status = await get_execution_status(host, token)
        if "Status: Accepted" in status or "Status: Compilation Error" in status:
            return status
        await asyncio.sleep(1)

    return _("Compilation timeout")


def _normalize_workspace_relative_path(path: str, *, allow_dot: bool = False) -> str:
    normalized = posixpath.normpath(str(path or "").strip().replace("\\", "/") or ".")
    if normalized in {"", "."}:
        if allow_dot:
            return "."
        raise ValueError("Empty workspace path.")
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"Invalid workspace path: {path}")
    return normalized


def _looks_like_text_content(content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _guess_workspace_mime_type(path: str, content: bytes, fallback: str | None = None) -> str:
    if fallback:
        return str(fallback)
    guessed, _encoding = mimetypes.guess_type(path)
    if guessed:
        return guessed
    if _looks_like_text_content(content):
        return "text/plain"
    return "application/octet-stream"


def _build_python_workspace_payload(request: PythonExecutionRequest) -> dict[str, Any]:
    mode = str(request.mode or "inline").strip().lower() or "inline"
    if mode not in {"inline", "script"}:
        raise ValueError(f"Unsupported Python execution mode: {request.mode}")

    cwd = _normalize_workspace_relative_path(request.cwd or ".", allow_dot=True)
    entrypoint = None
    if mode == "script":
        entrypoint = _normalize_workspace_relative_path(request.entrypoint or "", allow_dot=False)

    normalized_dirs: set[str] = set()
    for directory in request.workspace_directories or ():
        normalized = _normalize_workspace_relative_path(directory, allow_dot=True)
        if normalized != ".":
            normalized_dirs.add(normalized)

    workspace_files: list[dict[str, str]] = []
    file_paths: set[str] = set()
    total_bytes = 0
    for file in request.workspace_files or ():
        relative_path = _normalize_workspace_relative_path(file.path, allow_dot=False)
        if relative_path in file_paths:
            raise ValueError(f"Duplicate workspace path: {relative_path}")
        file_paths.add(relative_path)
        total_bytes += len(file.content)
        workspace_files.append(
            {
                "path": relative_path,
                "content_b64": base64.b64encode(file.content).decode("utf-8"),
                "mime_type": str(file.mime_type or "application/octet-stream"),
            }
        )
        parent = posixpath.dirname(relative_path)
        while parent not in {"", "."}:
            normalized_dirs.add(parent)
            parent = posixpath.dirname(parent)

    if len(workspace_files) > PYTHON_WORKSPACE_MAX_FILES:
        raise ValueError(
            f"Python workspace is too large: {len(workspace_files)} files exceeds the limit of {PYTHON_WORKSPACE_MAX_FILES}."
        )
    if total_bytes > PYTHON_WORKSPACE_MAX_BYTES:
        raise ValueError(
            f"Python workspace is too large: {total_bytes} bytes exceeds the limit of {PYTHON_WORKSPACE_MAX_BYTES}."
        )

    if entrypoint and entrypoint not in file_paths:
        raise ValueError(f"Script is outside the synchronized workspace: {entrypoint}")

    if cwd != ".":
        inside_workspace = (
            cwd in normalized_dirs
            or cwd in file_paths
            or any(path.startswith(f"{cwd}/") for path in file_paths)
            or any(path.startswith(f"{cwd}/") for path in normalized_dirs)
        )
        if not inside_workspace:
            normalized_dirs.add(cwd)

    return {
        "mode": mode,
        "code": str(request.code or ""),
        "entrypoint": entrypoint,
        "cwd": cwd,
        "directories": sorted(normalized_dirs),
        "files": workspace_files,
    }


def _decode_python_workspace_result(result: Judge0ExecutionResult) -> PythonExecutionResult:
    if result.status_description != "Accepted":
        return PythonExecutionResult(
            status_description=result.status_description,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    marker_index = result.stdout.rfind(PYTHON_WORKSPACE_RESULT_MARKER)
    if marker_index < 0:
        stderr = str(result.stderr or "").strip()
        if stderr:
            stderr = f"{stderr}\nInvalid Python workspace response from Judge0."
        else:
            stderr = "Invalid Python workspace response from Judge0."
        return PythonExecutionResult(
            status_description="Runtime Error",
            stdout=result.stdout,
            stderr=stderr,
        )

    encoded_envelope = result.stdout[marker_index + len(PYTHON_WORKSPACE_RESULT_MARKER):].strip()
    try:
        envelope = json.loads(base64.b64decode(encoded_envelope).decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to decode Python workspace response: %s", exc)
        stderr = str(result.stderr or "").strip()
        if stderr:
            stderr = f"{stderr}\nInvalid Python workspace payload from Judge0."
        else:
            stderr = "Invalid Python workspace payload from Judge0."
        return PythonExecutionResult(
            status_description="Runtime Error",
            stdout=result.stdout,
            stderr=stderr,
        )

    output_files: list[PythonWorkspaceFile] = []
    total_bytes = 0
    for item in envelope.get("files") or []:
        path = _normalize_workspace_relative_path(item.get("path"), allow_dot=False)
        content = base64.b64decode(item.get("content_b64") or "")
        total_bytes += len(content)
        output_files.append(
            PythonWorkspaceFile(
                path=path,
                content=content,
                mime_type=_guess_workspace_mime_type(
                    path,
                    content,
                    fallback=item.get("mime_type"),
                ),
            )
        )

    stdout = str(envelope.get("stdout") or "")
    stderr = str(envelope.get("stderr") or "")
    status_description = str(envelope.get("status") or "Accepted")

    if len(output_files) > PYTHON_WRITEBACK_MAX_FILES or total_bytes > PYTHON_WRITEBACK_MAX_BYTES:
        warning = (
            f"Workspace write-back skipped because the result exceeded limits "
            f"({len(output_files)} files, {total_bytes} bytes)."
        )
        stderr = f"{stderr}\n{warning}".strip()
        output_files = []

    return PythonExecutionResult(
        status_description=status_description,
        stdout=stdout,
        stderr=stderr,
        output_files=tuple(output_files),
    )


async def execute_python_request(host: str, request: PythonExecutionRequest) -> PythonExecutionResult:
    if (
        str(request.mode or "inline").strip().lower() == "inline"
        and not tuple(request.workspace_files or ())
        and not tuple(request.workspace_directories or ())
        and str(request.cwd or ".").strip() in {"", "."}
    ):
        result = await execute_code_result(
            host,
            request.code,
            language="python",
            timeout=int(request.timeout or 5),
        )
        return PythonExecutionResult(
            status_description=result.status_description,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    payload = _build_python_workspace_payload(request)
    raw_result = await execute_code_result(
        host,
        _JUDGE0_PYTHON_WORKSPACE_WRAPPER,
        language="python",
        input_data=json.dumps(payload),
        timeout=int(request.timeout or 5),
    )
    return _decode_python_workspace_result(raw_result)


async def execute_code(
    host: str,
    code: str,
    language: Union[str, int] = "python",
    input_data: Optional[str] = None,
    timeout: int = 5,
) -> str:
    """Execute code and return output."""
    result = await execute_code_result(
        host,
        code,
        language=language,
        input_data=input_data,
        timeout=timeout,
    )
    return format_execution_result(result)


async def execute_code_result(
    host: str,
    code: str,
    language: Union[str, int] = "python",
    input_data: Optional[str] = None,
    timeout: int = 5,
) -> Judge0ExecutionResult:
    """Execute code and return a structured result."""
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    encoded_input_data = (
        base64.b64encode(input_data.encode("utf-8")).decode("utf-8") if input_data else ""
    )
    data = {
        "source_code": encoded_code,
        "language_id": language_id,
        "stdin": encoded_input_data,
        "cpu_time_limit": timeout,
        "memory_limit": 128000,
    }
    response = await api_request("POST", f"{host}/submissions?base64_encoded=true&wait=true", data=data)
    stdout = response.get("stdout", "")
    decoded_stdout = base64.b64decode(stdout).decode("utf-8") if stdout else ""
    stderr = response.get("stderr", "")
    decoded_stderr = base64.b64decode(stderr).decode("utf-8") if stderr else ""
    status = response.get("status", {}).get("description", "Unknown")
    return Judge0ExecutionResult(
        status_description=status,
        stdout=decoded_stdout,
        stderr=decoded_stderr,
    )


def format_execution_result(result: Judge0ExecutionResult) -> str:
    return f"Status: {result.status_description}\nStdout: {result.stdout}\nStderr: {result.stderr}"


async def run_code_with_input(
    host: str,
    code: str,
    language: Union[str, int] = "python",
    inputs: List[str] = None,
) -> str:
    """Run code with multiple inputs."""
    if inputs is None:
        inputs = []
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    results = []
    for inp in inputs:
        encoded_input_data = base64.b64encode(inp.encode("utf-8")).decode("utf-8") if inp else ""
        data = {
            "source_code": encoded_code,
            "language_id": language_id,
            "stdin": encoded_input_data,
            "cpu_time_limit": 5,
            "memory_limit": 128000,
        }
        response = await api_request("POST", f"{host}/submissions?base64_encoded=true&wait=true", data=data)
        stdout = response.get("stdout", "")
        decoded_stdout = base64.b64decode(stdout).decode("utf-8") if stdout else ""
        stderr = response.get("stderr", "")
        decoded_stderr = base64.b64decode(stderr).decode("utf-8") if stderr else ""
        status = response.get("status", {}).get("description", "Unknown")
        results.append(f"Input: {inp}\nStatus: {status}\nStdout: {decoded_stdout}\nStderr: {decoded_stderr}")
    return "\n\n".join(results)
