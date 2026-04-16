from __future__ import annotations

import json
import logging
import posixpath
import re
import shlex
from fnmatch import fnmatch
from dataclasses import dataclass, field
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from typing import Any

from django.utils import timezone

from nova.api_tools import service as api_tools_service
from nova.caldav import service as caldav_service
from nova.continuous.tools.conversation_tools import conversation_get, conversation_search
from nova.exec_runner import service as exec_runner_service
from nova.memory.service import MEMORY_ROOT
from nova.mcp import service as mcp_service
from nova.models.Thread import Thread
from nova.plugins.mail import service as mail_service
from nova.plugins.python import service as python_service
from nova.runtime.commands import (
    merge_command_outputs,
    resolve_boolean_command_status,
    should_execute_segment,
)
from nova.runtime.commands import filesystem as filesystem_commands
from nova.runtime.commands import integrations as integration_commands
from nova.runtime.commands import memory as memory_commands
from nova.runtime.commands import web as web_commands
from nova.runtime.commands import webapp as webapp_commands
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.vfs import HISTORY_ROOT, INBOX_ROOT, VFSError, VirtualFileSystem, normalize_vfs_path
from nova.webdav.service import WEBDAV_VFS_ROOT
from nova.webapp import service as webapp_service
from nova.web.browser_service import BrowserSession, BrowserSessionError
from nova.web.download_service import download_http_file

from .terminal_metrics import (
    FAILURE_KIND_COMMAND_ERROR,
    FAILURE_KIND_INVALID_ARGUMENTS,
    FAILURE_KIND_PARSE_ERROR,
    FAILURE_KIND_UNKNOWN_COMMAND,
    FAILURE_KIND_UNSUPPORTED_SYNTAX,
    classify_terminal_failure,
    normalize_head_command,
    record_terminal_command_failure,
    sanitize_terminal_command,
)

class TerminalCommandError(Exception):
    def __init__(
        self,
        message: str,
        *,
        failure_kind: str | None = None,
        execution_result: "ShellExecutionResult | None" = None,
        segment_failures: list["SegmentExecutionFailure"] | None = None,
    ):
        super().__init__(message)
        self.failure_kind = str(failure_kind or "").strip() or classify_terminal_failure(message)
        self.execution_result = execution_result
        self.segment_failures = list(
            segment_failures
            or (execution_result.segment_failures if execution_result is not None else [])
        )


logger = logging.getLogger(__name__)

BROWSER_SINGLE_PANE_ERROR = web_commands.BROWSER_SINGLE_PANE_ERROR
BROWSER_DEFAULT_ELEMENT_ATTRIBUTES = web_commands.BROWSER_DEFAULT_ELEMENT_ATTRIBUTES


@dataclass(slots=True, frozen=True)
class ParsedShellCommand:
    raw: str
    pipeline: list[list[str]]
    input_path: str | None = None
    output_path: str | None = None
    output_append: bool = False
    operator_before: str | None = None


@dataclass(slots=True, frozen=True)
class ParsedShellProgram:
    segments: list[ParsedShellCommand]


@dataclass(slots=True)
class ParsedDownloadCommand:
    url: str
    output_path: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""


@dataclass(slots=True, frozen=True)
class ParsedPythonCommand:
    output_path: str | None = None
    workdir: str | None = None
    inline_code: str | None = None
    script_path: str | None = None


@dataclass(slots=True, frozen=True)
class SegmentExecutionFailure:
    segment_index: int
    command: str
    head_command: str
    failure_kind: str
    error: str


@dataclass(slots=True, frozen=True)
class ShellStageResult:
    stdout: str = ""
    stderr: str = ""
    status: int = 0
    failure_kind: str = ""
    status_label: str = ""
    display_text: str = ""


@dataclass(slots=True, frozen=True)
class ShellSegmentResult:
    segment_index: int
    command: str
    head_command: str
    stdout: str = ""
    stderr: str = ""
    status: int | None = 0
    failure_kind: str = ""
    skipped: bool = False
    status_label: str = ""
    display_text: str = ""

    def render_text(self) -> str:
        if self.skipped:
            return ""
        if self.display_text:
            return str(self.display_text or "")

        if self.status_label:
            lines = [f"Status: {self.status_label}"]
            lines.append(f"Stdout: {self.stdout}" if self.stdout else "Stdout: ")
            if self.stderr:
                lines.append(f"Stderr: {self.stderr.rstrip()}")
            return "\n".join(lines)

        text = str(self.stdout or "")
        stderr = str(self.stderr or "").rstrip("\n")
        if stderr:
            if text and not text.endswith("\n"):
                text = f"{text}\n"
            if "\n" in stderr:
                text = f"{text}stderr:\n{stderr}"
            else:
                text = f"{text}stderr: {stderr}"
        return text


@dataclass(slots=True, frozen=True)
class ShellExecutionResult:
    stdout: str = ""
    stderr: str = ""
    status: int = 0
    failure_kind: str = ""
    segments: list[ShellSegmentResult] = field(default_factory=list)

    @property
    def executed_segment_indexes(self) -> list[int]:
        return [segment.segment_index for segment in self.segments if not segment.skipped]

    @property
    def skipped_segment_indexes(self) -> list[int]:
        return [segment.segment_index for segment in self.segments if segment.skipped]

    @property
    def failed_segment_indexes(self) -> list[int]:
        return [
            segment.segment_index
            for segment in self.segments
            if not segment.skipped and int(segment.status or 0) != 0
        ]

    @property
    def segment_failures(self) -> list[SegmentExecutionFailure]:
        failures: list[SegmentExecutionFailure] = []
        for segment in self.segments:
            if segment.skipped or int(segment.status or 0) == 0:
                continue
            error_text = segment.render_text() or segment.stderr or "Command failed."
            failures.append(
                SegmentExecutionFailure(
                    segment_index=segment.segment_index,
                    command=segment.command,
                    head_command=segment.head_command,
                    failure_kind=str(segment.failure_kind or classify_terminal_failure(error_text)),
                    error=error_text,
                )
            )
        return failures

    def render_text(self) -> str:
        rendered: list[str] = []
        for segment in self.segments:
            text = segment.render_text()
            if text:
                rendered.append(text)
        merged = ""
        for text in rendered:
            if not merged:
                merged = text
            elif merged.endswith("\n") or text.startswith("\n"):
                merged = f"{merged}{text}"
            else:
                merged = f"{merged}\n{text}"
        return merged


class TerminalExecutor:
    NOVA_BUILTIN_COMMANDS = {
        "api",
        "browse",
        "calendar",
        "cat",
        "cd",
        "cp",
        "curl",
        "date",
        "echo",
        "false",
        "file",
        "find",
        "grep",
        "head",
        "history",
        "la",
        "ll",
        "ls",
        "mail",
        "mcp",
        "memory",
        "mkdir",
        "mv",
        "pwd",
        "printf",
        "python",
        "rm",
        "rmdir",
        "search",
        "sort",
        "tail",
        "tee",
        "touch",
        "true",
        "wc",
        "webapp",
        "wget",
    }
    HOST_MEDIATED_COMMANDS = {
        "api",
        "browse",
        "calendar",
        "curl",
        "date",
        "history",
        "mail",
        "mcp",
        "memory",
        "python",
        "search",
        "webapp",
        "wget",
    }
    HOST_MEDIATED_PATH_PREFIXES = (
        f"{MEMORY_ROOT}/",
        f"{WEBDAV_VFS_ROOT}/",
    )

    def __init__(self, *, vfs: VirtualFileSystem, capabilities: TerminalCapabilities):
        self.vfs = vfs
        self.capabilities = capabilities
        self._mailbox_registry_cache = None
        self._calendar_registry_cache = None
        self._last_search_results: list[dict] = []
        self._browser_session: BrowserSession | None = None
        self.realtime_task_id = None
        self.realtime_channel_layer = None
        self.last_execution_plane = "nova"

    def _iter_shell_heads_for_routing(self, raw: str) -> list[str]:
        segments = self._split_shell_segments(str(raw or "").strip())
        heads: list[str] = []
        for _operator, segment in segments:
            try:
                lexer = shlex.shlex(segment, posix=True, punctuation_chars="|<>")
                lexer.whitespace_split = True
                lexer.commenters = ""
                tokens = list(lexer)
            except ValueError:
                return []
            stage: list[str] = []
            for token in tokens:
                if token == "|":
                    if stage:
                        heads.append(str(stage[0] or "").strip())
                    stage = []
                    continue
                if token in {"<", ">", ">>"}:
                    stage = list(stage)
                    continue
                stage.append(token)
            if stage:
                heads.append(str(stage[0] or "").strip())
        return [head for head in heads if head]

    def _command_uses_host_mediated_paths(self, raw: str) -> bool:
        text = str(raw or "")
        return any(
            re.search(
                rf"(?<![A-Za-z0-9_.-]){re.escape(root)}(?![A-Za-z0-9_.-])",
                text,
            )
            for root in (MEMORY_ROOT, WEBDAV_VFS_ROOT)
        )

    def _should_route_command_to_sandbox(self, raw: str) -> bool:
        if not exec_runner_service.exec_runner_is_configured():
            return False
        if self._command_uses_host_mediated_paths(raw):
            return False
        heads = self._iter_shell_heads_for_routing(raw)
        if not heads:
            return False
        if any(head in self.HOST_MEDIATED_COMMANDS for head in heads):
            return False
        if any(head not in self.NOVA_BUILTIN_COMMANDS for head in heads):
            return True
        try:
            self._parse_shell_command(raw)
        except TerminalCommandError as exc:
            if str(getattr(exc, "failure_kind", "") or "") in {
                FAILURE_KIND_PARSE_ERROR,
                FAILURE_KIND_UNSUPPORTED_SYNTAX,
            }:
                return True
            return False
        return False

    @staticmethod
    def _render_sandbox_display_text(result: exec_runner_service.SandboxShellResult) -> str:
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "").rstrip("\n")
        if not stderr:
            return stdout
        if stdout and not stdout.endswith("\n"):
            stdout = f"{stdout}\n"
        if "\n" in stderr:
            return f"{stdout}stderr:\n{stderr}"
        return f"{stdout}stderr: {stderr}"

    async def _execute_sandbox_result(self, command: str) -> ShellExecutionResult:
        sandbox_result, _sync_meta = await exec_runner_service.execute_sandbox_shell_command(
            vfs=self.vfs,
            command=command,
        )
        raw_status = int(sandbox_result.status or 0)
        head_command = normalize_head_command(command)
        stderr_text = str(sandbox_result.stderr or "")
        message = stderr_text or str(sandbox_result.stdout or "") or f"Exit status: {raw_status}"
        failure_kind = ""
        if raw_status != 0:
            if "command not found" in str(message).lower():
                failure_kind = FAILURE_KIND_UNKNOWN_COMMAND
            else:
                failure_kind = classify_terminal_failure(message)
        segment = ShellSegmentResult(
            segment_index=0,
            command=str(command or "").strip(),
            head_command=head_command,
            stdout=sandbox_result.stdout,
            stderr=stderr_text,
            status=raw_status,
            failure_kind=failure_kind,
            display_text=self._render_sandbox_display_text(
                exec_runner_service.SandboxShellResult(
                    stdout=sandbox_result.stdout,
                    stderr=stderr_text,
                    status=sandbox_result.status,
                    cwd_after=sandbox_result.cwd_after,
                )
            ),
        )
        return ShellExecutionResult(
            stdout=sandbox_result.stdout,
            stderr=stderr_text,
            status=raw_status,
            failure_kind=failure_kind,
            segments=[segment],
        )

    def _validate_shell_operators(self, raw: str) -> None:
        in_single = False
        in_double = False
        escaped = False
        index = 0
        while index < len(raw):
            char = raw[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\" and not in_single:
                escaped = True
                index += 1
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                index += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                index += 1
                continue
            if in_single or in_double:
                index += 1
                continue

            if raw.startswith("<<<", index) or raw.startswith("<<", index):
                raise TerminalCommandError(
                    "Heredocs and << redirections are not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            if raw.startswith("2>&1", index) or raw.startswith("2>>", index) or raw.startswith("2>", index):
                raise TerminalCommandError(
                    "stderr redirections are not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            if raw.startswith("&>", index):
                raise TerminalCommandError(
                    "Combined stdout/stderr redirections are not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            if raw.startswith("$(", index) or char == "`":
                raise TerminalCommandError(
                    "Shell substitutions are not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            index += 1

    @staticmethod
    def _missing_segment_error(operator: str) -> TerminalCommandError:
        label = operator or ";"
        return TerminalCommandError(
            f"Command chaining with {label} requires a command on both sides.",
            failure_kind=FAILURE_KIND_PARSE_ERROR,
        )

    def _split_shell_segments(self, raw: str) -> list[tuple[str | None, str]]:
        segments: list[tuple[str | None, str]] = []
        in_single = False
        in_double = False
        escaped = False
        start = 0
        operator_before: str | None = None
        index = 0
        while index < len(raw):
            char = raw[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\" and not in_single:
                escaped = True
                index += 1
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                index += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                index += 1
                continue
            if in_single or in_double:
                index += 1
                continue

            matched_operator: str | None = None
            operator_length = 0
            if raw.startswith("&&", index):
                matched_operator = "&&"
                operator_length = 2
            elif raw.startswith("||", index):
                matched_operator = "||"
                operator_length = 2
            elif char == ";":
                matched_operator = ";"
                operator_length = 1

            if matched_operator is None:
                index += 1
                continue

            segment = raw[start:index].strip()
            if not segment:
                raise self._missing_segment_error(matched_operator)
            segments.append((operator_before, segment))
            operator_before = matched_operator
            start = index + operator_length
            index += operator_length

        final_segment = raw[start:].strip()
        if not final_segment:
            raise self._missing_segment_error(operator_before or ";")
        segments.append((operator_before, final_segment))
        return segments

    def _tokenize_shell(self, command: str) -> list[str]:
        raw = str(command or "").strip()
        if not raw:
            raise TerminalCommandError("Empty command.", failure_kind=FAILURE_KIND_PARSE_ERROR)
        self._validate_shell_operators(raw)
        try:
            lexer = shlex.shlex(raw, posix=True, punctuation_chars="|<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            return list(lexer)
        except ValueError as exc:
            raise TerminalCommandError(
                f"Command parse error: {exc}",
                failure_kind=FAILURE_KIND_PARSE_ERROR,
            ) from exc

    def _parse_shell_segment(self, command: str) -> ParsedShellCommand:
        raw = str(command or "").strip()
        tokens = self._tokenize_shell(raw)
        if not tokens:
            raise TerminalCommandError("Empty command.", failure_kind=FAILURE_KIND_PARSE_ERROR)

        pipeline: list[list[str]] = []
        current: list[str] = []
        input_path: str | None = None
        output_path: str | None = None
        output_append = False

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "|":
                if output_path is not None:
                    raise TerminalCommandError(
                        "Output redirection must appear at the end of the command.",
                        failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                    )
                if not current:
                    raise TerminalCommandError(
                        "Pipes require a command on both sides.",
                        failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                    )
                pipeline.append(current)
                current = []
                index += 1
                continue
            if token == "<":
                index += 1
                if index >= len(tokens) or tokens[index] in {"|", "<", ">", ">>"}:
                    raise TerminalCommandError(
                        "Missing path after <",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                if pipeline:
                    raise TerminalCommandError(
                        "Input redirection is supported only for the first command in the pipeline.",
                        failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                    )
                if input_path is not None:
                    raise TerminalCommandError(
                        "Only one input redirection is supported.",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                input_path = tokens[index]
                index += 1
                continue
            if token in {">", ">>"}:
                index += 1
                if index >= len(tokens) or tokens[index] in {"|", "<", ">", ">>"}:
                    raise TerminalCommandError(
                        f"Missing path after {token}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                if output_path is not None:
                    raise TerminalCommandError(
                        "Only one output redirection is supported.",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                output_path = tokens[index]
                output_append = token == ">>"
                if index != len(tokens) - 1:
                    raise TerminalCommandError(
                        "Output redirection must appear at the end of the command.",
                        failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                    )
                index += 1
                continue
            if token == "<<":
                raise TerminalCommandError(
                    "Heredocs are not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            current.append(token)
            index += 1

        if not current:
            raise TerminalCommandError(
                "Pipes require a command on both sides.",
                failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
            )
        pipeline.append(current)

        if output_path and any(self._command_uses_builtin_output(stage) for stage in pipeline):
            raise TerminalCommandError(
                "Shell output redirection cannot be combined with --output.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )

        return ParsedShellCommand(
            raw=raw,
            pipeline=pipeline,
            input_path=input_path,
            output_path=output_path,
            output_append=output_append,
        )

    def _parse_shell_command(self, command: str) -> ParsedShellProgram:
        raw = str(command or "").strip()
        if not raw:
            raise TerminalCommandError("Empty command.", failure_kind=FAILURE_KIND_PARSE_ERROR)
        self._validate_shell_operators(raw)
        segments = [
            ParsedShellCommand(
                raw=parsed_segment.raw,
                pipeline=parsed_segment.pipeline,
                input_path=parsed_segment.input_path,
                output_path=parsed_segment.output_path,
                output_append=parsed_segment.output_append,
                operator_before=operator_before,
            )
            for operator_before, segment in self._split_shell_segments(raw)
            for parsed_segment in [self._parse_shell_segment(segment)]
        ]
        return ParsedShellProgram(segments=segments)

    @staticmethod
    def _command_uses_builtin_output(tokens: list[str]) -> bool:
        return any(token in {"--output", "-o", "-O"} for token in tokens)

    async def _record_terminal_failure(self, command: str, error: TerminalCommandError) -> None:
        failure_kind = str(getattr(error, "failure_kind", "") or classify_terminal_failure(str(error)))
        sanitized_command = sanitize_terminal_command(command)
        payload = {
            "failure_kind": failure_kind,
            "head_command": normalize_head_command(command),
            "command": sanitized_command,
            "error": str(error),
        }
        logger.warning(
            "terminal_command_failed failure_kind=%s head_command=%s command=%s error=%s",
            payload["failure_kind"],
            payload["head_command"],
            payload["command"],
            payload["error"],
            extra={"terminal_failure": payload},
        )
        await record_terminal_command_failure(
            command=command,
            failure_kind=failure_kind,
            error_message=str(error),
        )

    @staticmethod
    def _merge_command_outputs(outputs: list[str]) -> str:
        return merge_command_outputs(outputs)

    @staticmethod
    def _should_execute_segment(operator: str | None, previous_status: int) -> bool:
        return should_execute_segment(operator, previous_status)

    async def _run_shell_segment_result(
        self,
        parsed: ParsedShellCommand,
        *,
        segment_index: int,
    ) -> ShellSegmentResult:
        stdin_text = None
        head_command = normalize_head_command(parsed.raw)
        if parsed.input_path:
            try:
                stdin_text = await self.vfs.read_text(parsed.input_path)
            except VFSError as exc:
                message = str(exc)
                return ShellSegmentResult(
                    segment_index=segment_index,
                    command=parsed.raw,
                    head_command=head_command,
                    stderr=message,
                    status=1,
                    failure_kind=classify_terminal_failure(message),
                )

        output = ""
        stderr_parts: list[str] = []
        display_text = ""
        for index, tokens in enumerate(parsed.pipeline):
            capture_output = index < len(parsed.pipeline) - 1 or parsed.output_path is not None
            stage_result = await self._execute_stage_result(
                tokens,
                stdin_text=stdin_text,
                capture_output=capture_output,
            )
            if stage_result.stderr:
                stderr_parts.append(stage_result.stderr)
            if stage_result.status != 0:
                return ShellSegmentResult(
                    segment_index=segment_index,
                    command=parsed.raw,
                    head_command=head_command,
                    stderr=self._merge_command_outputs(stderr_parts),
                    status=1,
                    failure_kind=stage_result.failure_kind or classify_terminal_failure(
                        self._merge_command_outputs(stderr_parts)
                    ),
                    status_label=stage_result.status_label,
                    display_text=stage_result.display_text,
                )
            output = stage_result.stdout
            stdin_text = stage_result.stdout
            display_text = stage_result.display_text

        if parsed.output_path is not None:
            try:
                written = await self._write_shell_output(
                    parsed.output_path,
                    output,
                    append=parsed.output_append,
                )
            except TerminalCommandError as exc:
                message = str(exc)
                if message:
                    stderr_parts.append(message)
                return ShellSegmentResult(
                    segment_index=segment_index,
                    command=parsed.raw,
                    head_command=head_command,
                    stderr=self._merge_command_outputs(stderr_parts),
                    status=1,
                    failure_kind=str(getattr(exc, "failure_kind", "") or classify_terminal_failure(message)),
                )
            output = self._format_write_result(
                f"Wrote {len(output.encode('utf-8'))} bytes to {written.path}",
                written,
            )
            display_text = output

        return ShellSegmentResult(
            segment_index=segment_index,
            command=parsed.raw,
            head_command=head_command,
            stdout=output or "",
            stderr=self._merge_command_outputs(stderr_parts),
            status=0,
            display_text=display_text,
        )

    async def _run_shell_command_result(self, parsed: ParsedShellProgram) -> ShellExecutionResult:
        segment_results: list[ShellSegmentResult] = []
        last_status = 0

        for segment_index, segment in enumerate(parsed.segments, start=1):
            if not self._should_execute_segment(segment.operator_before, last_status):
                segment_results.append(
                    ShellSegmentResult(
                        segment_index=segment_index,
                        command=segment.raw,
                        head_command=normalize_head_command(segment.raw),
                        status=None,
                        skipped=True,
                    )
                )
                continue

            segment_result = await self._run_shell_segment_result(segment, segment_index=segment_index)
            segment_results.append(segment_result)
            last_status = int(segment_result.status or 0)

        executed_segments = [segment for segment in segment_results if not segment.skipped]
        stdout = self._merge_command_outputs([segment.stdout for segment in executed_segments])
        stderr = self._merge_command_outputs([segment.stderr for segment in executed_segments])
        status = int(executed_segments[-1].status or 0) if executed_segments else 0
        failure_kind = ""
        if status != 0:
            for segment in reversed(executed_segments):
                if int(segment.status or 0) != 0:
                    failure_kind = str(segment.failure_kind or classify_terminal_failure(segment.render_text()))
                    break

        return ShellExecutionResult(
            stdout=stdout,
            stderr=stderr,
            status=status,
            failure_kind=failure_kind,
            segments=segment_results,
        )

    async def _execute_stage_result(
        self,
        tokens: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> ShellStageResult:
        if not tokens:
            return ShellStageResult(
                stderr="Empty pipeline stage.",
                status=1,
                failure_kind=FAILURE_KIND_PARSE_ERROR,
        )
        name = str(tokens[0] or "").strip()
        args = tokens[1:]
        try:
            boolean_status = resolve_boolean_command_status(name)
            if boolean_status is not None:
                return ShellStageResult(stdout="", status=boolean_status)
            if name == "python":
                return await self._cmd_python_result(args)
            return ShellStageResult(
                stdout=await self._dispatch_command(
                    name,
                    args,
                    stdin_text=stdin_text,
                    capture_output=capture_output,
                )
            )
        except TerminalCommandError as exc:
            message = str(exc)
            return ShellStageResult(
                stderr=message,
                status=1,
                failure_kind=str(getattr(exc, "failure_kind", "") or classify_terminal_failure(message)),
            )

    async def _dispatch_command(
        self,
        name: str,
        args: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        if name == "pwd":
            return self.vfs.cwd
        if name == "echo":
            return await self._cmd_echo(args)
        if name == "printf":
            return await self._cmd_printf(args)
        if name in {"ls", "la", "ll"}:
            effective_args = list(args)
            if name == "la":
                effective_args = ["-la", *effective_args]
            elif name == "ll":
                effective_args = ["-l", *effective_args]
            return await self._cmd_ls(effective_args)
        if name == "cd":
            return await self._cmd_cd(args)
        if name == "cat":
            return await self._cmd_cat(args, stdin_text=stdin_text)
        if name == "head":
            return await self._cmd_head_tail(args, tail=False, stdin_text=stdin_text)
        if name == "tail":
            return await self._cmd_head_tail(args, tail=True, stdin_text=stdin_text)
        if name == "mkdir":
            return await self._cmd_mkdir(args)
        if name == "rmdir":
            return await self._cmd_rmdir(args)
        if name == "touch":
            return await self._cmd_touch(args)
        if name == "tee":
            return await self._cmd_tee(args, stdin_text=stdin_text)
        if name == "cp":
            return await self._cmd_cp(args)
        if name == "mv":
            return await self._cmd_mv(args)
        if name == "rm":
            return await self._cmd_rm(args)
        if name == "find":
            return await self._cmd_find(args)
        if name == "sort":
            return await self._cmd_sort(args, stdin_text=stdin_text)
        if name == "file":
            return await self._cmd_file(args)
        if name == "grep":
            return await self._cmd_grep(args, stdin_text=stdin_text)
        if name == "wc":
            return await self._cmd_wc(args, stdin_text=stdin_text)
        if name == "search":
            return await self._cmd_search(args, capture_output=capture_output)
        if name == "browse":
            return await self._cmd_browse(args, capture_output=capture_output)
        if name == "history":
            return await self._cmd_history(args)
        if name == "date":
            return await self._cmd_date(args)
        if name == "wget":
            return await self._cmd_wget(args)
        if name == "curl":
            return await self._cmd_curl(args, capture_output=capture_output)
        if name == "calendar":
            return await self._cmd_calendar(args)
        if name == "mail":
            return await self._cmd_mail(args)
        if name == "memory":
            return await self._cmd_memory(args)
        if name == "mcp":
            return await self._cmd_mcp(args, stdin_text=stdin_text, capture_output=capture_output)
        if name == "api":
            return await self._cmd_api(args, stdin_text=stdin_text, capture_output=capture_output)
        if name == "webapp":
            return await self._cmd_webapp(args)
        if name == "python":
            return await self._cmd_python(args)

        raise TerminalCommandError(
            f"Unknown command: {name}",
            failure_kind="unknown_command",
        )

    async def _record_segment_failures(self, failures: list[SegmentExecutionFailure]) -> None:
        for failure in failures:
            await record_terminal_command_failure(
                command=failure.command,
                failure_kind=failure.failure_kind,
                error_message=failure.error,
            )

    async def execute_result(self, command: str) -> ShellExecutionResult:
        self.vfs.remember_command(command)
        try:
            if self._should_route_command_to_sandbox(command):
                self.last_execution_plane = "sandbox"
                result = await self._execute_sandbox_result(command)
                if result.segment_failures:
                    await self._record_segment_failures(result.segment_failures)
                return result
            self.last_execution_plane = "nova"
            parsed = self._parse_shell_command(command)
            result = await self._run_shell_command_result(parsed)
            if result.segment_failures:
                await self._record_segment_failures(result.segment_failures)
            return result
        except TerminalCommandError as exc:
            await self._record_terminal_failure(command, exc)
            raise
        except Exception as exc:
            wrapped = TerminalCommandError(
                str(exc),
                failure_kind=FAILURE_KIND_COMMAND_ERROR,
            )
            await self._record_terminal_failure(command, wrapped)
            raise

    async def execute(self, command: str) -> str:
        result = await self.execute_result(command)
        rendered = result.render_text()
        if result.status != 0:
            executed_segments = [segment for segment in result.segments if not segment.skipped]
            if len(executed_segments) == 1:
                segment = executed_segments[0]
                message = (
                    segment.display_text
                    or segment.stderr
                    or segment.render_text()
                    or "Command failed."
                )
            else:
                message = rendered or result.stderr or "Command failed."
            raise TerminalCommandError(
                message,
                failure_kind=result.failure_kind or classify_terminal_failure(message),
                execution_result=result,
            )
        return rendered

    async def _cmd_echo(self, args: list[str]) -> str:
        append_newline = True
        remaining = list(args)
        if remaining and remaining[0] == "-n":
            append_newline = False
            remaining = remaining[1:]
        text = " ".join(remaining)
        return text if not append_newline else f"{text}\n"

    @staticmethod
    def _parse_short_flags(
        args: list[str],
        *,
        command_name: str,
        supported_flags: set[str],
        allow_numeric_count: bool = False,
    ) -> tuple[set[str], list[str], int | None]:
        flags: set[str] = set()
        positionals: list[str] = []
        numeric_count: int | None = None
        end_of_options = False

        for token in args:
            if end_of_options:
                positionals.append(token)
                continue
            if token == "--":
                end_of_options = True
                continue
            if token == "-" or not token.startswith("-") or len(token) == 1:
                positionals.append(token)
                continue
            if allow_numeric_count and re.fullmatch(r"-\d+", token):
                if numeric_count is not None:
                    raise TerminalCommandError(
                        f"Usage: {command_name}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                numeric_count = int(token[1:])
                continue

            for flag in token[1:]:
                if flag not in supported_flags:
                    raise TerminalCommandError(
                        f"Unsupported {command_name.split()[0]} flag: -{flag}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                flags.add(flag)

        return flags, positionals, numeric_count

    @staticmethod
    def _count_text_lines(content: str) -> int:
        text = str(content or "")
        if not text:
            return 0
        return len(text.splitlines())

    @staticmethod
    def _number_lines(content: str) -> str:
        lines = str(content or "").splitlines()
        return "\n".join(f"{index}\t{line}" for index, line in enumerate(lines, start=1))

    @staticmethod
    def _parse_ls_flags(args: list[str]) -> tuple[dict[str, bool], list[str]]:
        options = {
            "show_all": False,
            "long_format": False,
            "one_per_line": False,
            "human_readable": False,
            "recursive": False,
        }
        paths: list[str] = []
        for token in args:
            if token.startswith("-") and token != "-":
                for flag in token[1:]:
                    if flag == "a":
                        options["show_all"] = True
                    elif flag == "l":
                        options["long_format"] = True
                    elif flag == "1":
                        options["one_per_line"] = True
                    elif flag == "h":
                        options["human_readable"] = True
                    elif flag == "R":
                        options["recursive"] = True
                    else:
                        raise TerminalCommandError(
                            f"Unsupported ls flag: -{flag}",
                            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                        )
                continue
            paths.append(token)
        return options, paths

    @staticmethod
    def _format_human_size(size: int | None) -> str:
        value = float(size or 0)
        units = ["B", "K", "M", "G", "T", "P"]
        unit_index = 0
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)}{units[unit_index]}"
        if value >= 10 or float(value).is_integer():
            return f"{value:.0f}{units[unit_index]}"
        return f"{value:.1f}{units[unit_index]}"

    @staticmethod
    def _format_ls_mode(entry_type: str) -> str:
        return "drwxr-xr-x" if entry_type == "dir" else "-rw-r--r--"

    async def _lookup_ls_entry(self, normalized_path: str) -> dict[str, str | int | None]:
        basename = posixpath.basename(normalized_path.rstrip("/")) or normalized_path
        parent = posixpath.dirname(normalized_path.rstrip("/")) or "/"
        try:
            parent_entries = await self.vfs.list_dir(parent)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        for entry in parent_entries:
            candidate_path = normalize_vfs_path(str(entry.get("path") or ""), cwd=parent)
            if candidate_path == normalized_path:
                return entry
        return {"name": basename, "path": normalized_path, "type": "file"}

    async def _resolve_vfs_entry(self, raw_path: str) -> dict[str, str | int | None]:
        normalized = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(normalized):
            raise TerminalCommandError(f"Path not found: {normalized}")
        if await self.vfs.is_dir(normalized):
            basename = posixpath.basename(normalized.rstrip("/")) or normalized
            return {"name": basename, "path": normalized, "type": "dir"}
        return await self._lookup_ls_entry(normalized)

    async def _expand_ls_target(self, raw_path: str) -> list[str]:
        raw = str(raw_path or "").strip()
        if "*" not in raw and "?" not in raw:
            normalized = normalize_vfs_path(raw, cwd=self.vfs.cwd)
            if not await self.vfs.path_exists(normalized):
                raise TerminalCommandError(f"Path not found: {normalized}")
            return [normalized]

        normalized = normalize_vfs_path(raw, cwd=self.vfs.cwd)
        parent = posixpath.dirname(normalized.rstrip("/")) or "/"
        pattern = posixpath.basename(normalized.rstrip("/"))
        if "*" in parent or "?" in parent:
            raise TerminalCommandError(
                "ls wildcard expansion is supported only in the final path segment.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if not await self.vfs.path_exists(parent) or not await self.vfs.is_dir(parent):
            raise TerminalCommandError(f"Directory not found: {parent}")

        try:
            entries = await self.vfs.list_dir(parent)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        matches = [
            normalize_vfs_path(str(entry.get("path") or ""), cwd=parent)
            for entry in entries
            if fnmatch(str(entry.get("name") or ""), pattern)
        ]
        if not matches:
            raise TerminalCommandError(
                f"No matches for pattern: {normalized}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        return matches

    def _format_ls_entry(
        self,
        entry: dict[str, str | int],
        *,
        long_format: bool,
        human_readable: bool = False,
    ) -> str:
        name = str(entry.get("name") or "")
        if entry.get("type") == "dir" and not name.endswith("/"):
            name = f"{name}/"
        if not long_format:
            return name
        size = "-"
        mime_type = "-"
        if entry.get("type") != "dir":
            raw_size = int(entry.get("size") if entry.get("size") is not None else 0)
            size = self._format_human_size(raw_size) if human_readable else str(raw_size)
            mime_type = str(entry.get("mime_type") or "application/octet-stream")
        return f"{self._format_ls_mode(str(entry.get('type') or 'file'))} {size} {mime_type} {name}"

    async def _render_ls_directory(
        self,
        normalized: str,
        *,
        show_all: bool,
        long_format: bool,
        human_readable: bool,
    ) -> list[str]:
        entries = await self.vfs.list_dir(normalized)
        rendered_entries = list(entries)
        if show_all:
            rendered_entries = [
                {"name": ".", "path": normalized, "type": "dir"},
                {
                    "name": "..",
                    "path": posixpath.dirname(normalized.rstrip("/")) or "/",
                    "type": "dir",
                },
                *rendered_entries,
            ]
        if not rendered_entries:
            return []
        return [
            self._format_ls_entry(
                entry,
                long_format=long_format,
                human_readable=human_readable,
            )
            for entry in rendered_entries
        ]

    async def _render_ls_recursive_sections(
        self,
        normalized: str,
        *,
        show_all: bool,
        long_format: bool,
        human_readable: bool,
    ) -> list[str]:
        sections: list[str] = []

        async def _walk(path: str) -> None:
            lines = await self._render_ls_directory(
                path,
                show_all=show_all,
                long_format=long_format,
                human_readable=human_readable,
            )
            sections.append(path if not lines else f"{path}:\n" + "\n".join(lines))
            entries = await self.vfs.list_dir(path)
            for entry in entries:
                if str(entry.get("type") or "") != "dir":
                    continue
                child_path = normalize_vfs_path(str(entry.get("path") or ""), cwd=path)
                await _walk(child_path)

        await _walk(normalized)
        return sections

    async def _cmd_ls(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_ls(self, args)

    async def _cmd_cd(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_cd(self, args)

    async def _cmd_cat(self, args: list[str], *, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_cat(self, args, stdin_text=stdin_text)

    async def _cmd_head_tail(self, args: list[str], *, tail: bool, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_head_tail(self, args, tail=tail, stdin_text=stdin_text)

    async def _cmd_mkdir(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_mkdir(self, args)

    async def _cmd_rmdir(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_rmdir(self, args)

    def _validate_text_write_path(self, raw_path: str) -> str:
        normalized = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
        if normalized.startswith("/skills"):
            raise TerminalCommandError("Writing into /skills is not supported.")
        return normalized

    def _validate_python_workspace_root(self, raw_path: str) -> str:
        normalized = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
        if normalized.startswith("/skills"):
            raise TerminalCommandError("Python workspaces cannot live inside /skills.")
        if normalized == INBOX_ROOT or normalized.startswith(f"{INBOX_ROOT}/"):
            raise TerminalCommandError("Copy files out of /inbox before using them as a Python workspace.")
        if normalized == HISTORY_ROOT or normalized.startswith(f"{HISTORY_ROOT}/"):
            raise TerminalCommandError("Copy files out of /history before using them as a Python workspace.")
        if normalized == MEMORY_ROOT or normalized.startswith(f"{MEMORY_ROOT}/"):
            raise TerminalCommandError("Copy files out of /memory into a normal workspace before running Python there.")
        if normalized == "/webdav" or normalized.startswith("/webdav/"):
            raise TerminalCommandError("Copy files out of /webdav before using them as a Python workspace.")
        return normalized

    @staticmethod
    def _decode_escaped_text(text: str) -> str:
        source = str(text or "")
        if "\\" not in source:
            return source
        parts: list[str] = []
        index = 0
        while index < len(source):
            char = source[index]
            if char != "\\" or index == len(source) - 1:
                parts.append(char)
                index += 1
                continue
            pair = source[index:index + 2]
            if pair == "\\n":
                parts.append("\n")
                index += 2
                continue
            if pair == "\\r":
                parts.append("\r")
                index += 2
                continue
            if pair == "\\t":
                parts.append("\t")
                index += 2
                continue
            if pair == '\\"':
                parts.append('"')
                index += 2
                continue
            if pair == "\\'":
                parts.append("'")
                index += 2
                continue
            if pair == "\\\\":
                parts.append("\\")
                index += 2
                continue
            parts.append(char)
            index += 1
        return "".join(parts)

    @staticmethod
    def _parse_printf_parts(format_text: str) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        literal: list[str] = []
        index = 0
        while index < len(format_text):
            char = format_text[index]
            if char != "%":
                literal.append(char)
                index += 1
                continue
            if index == len(format_text) - 1:
                raise TerminalCommandError(
                    "Invalid printf format: trailing %",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            specifier = format_text[index + 1]
            if specifier == "%":
                literal.append("%")
                index += 2
                continue
            if specifier not in {"s", "d", "i", "f"}:
                raise TerminalCommandError(
                    f"Unsupported printf placeholder: %{specifier}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            if literal:
                parts.append(("literal", "".join(literal)))
                literal = []
            parts.append(("spec", specifier))
            index += 2
        if literal:
            parts.append(("literal", "".join(literal)))
        return parts

    @staticmethod
    def _format_printf_value(specifier: str, value: str | None) -> str:
        if specifier == "s":
            return str(value or "")
        candidate = str(value or "0").strip() or "0"
        if specifier in {"d", "i"}:
            try:
                return str(int(candidate))
            except ValueError:
                try:
                    return str(int(float(candidate)))
                except (TypeError, ValueError) as exc:
                    raise TerminalCommandError(
                        f"printf: invalid %{specifier} value: {candidate}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    ) from exc
        if specifier == "f":
            try:
                return f"{float(candidate):f}"
            except (TypeError, ValueError) as exc:
                raise TerminalCommandError(
                    f"printf: invalid %f value: {candidate}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                ) from exc
        return str(value or "")

    async def _cmd_printf(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_printf(self, args)

    async def _write_shell_output(self, raw_path: str, content: str, *, append: bool):
        normalized = self._validate_text_write_path(raw_path)
        if append and await self.vfs.path_exists(normalized):
            try:
                existing_text = await self.vfs.read_text(normalized)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            content = f"{existing_text}{content}"
        try:
            return await self._write_file_and_notify(
                normalized,
                str(content).encode("utf-8"),
                mime_type="text/plain",
                overwrite=True,
            )
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_touch(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_touch(self, args)

    async def _cmd_tee(self, args: list[str], *, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_tee(self, args, stdin_text=stdin_text)

    async def _cmd_cp(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_cp(self, args)

    async def _cmd_mv(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_mv(self, args)

    async def _cmd_rm(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_rm(self, args)

    async def _cmd_find(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_find(self, args)

    @staticmethod
    def _sort_text_lines(content: str) -> str:
        text = str(content or "")
        lines = text.splitlines()
        if not lines:
            return ""
        rendered = "\n".join(sorted(lines))
        if text.endswith("\n"):
            rendered += "\n"
        return rendered

    async def _cmd_sort(self, args: list[str], *, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_sort(self, args, stdin_text=stdin_text)

    async def _cmd_file(self, args: list[str]) -> str:
        return await filesystem_commands.cmd_file(self, args)

    async def _cmd_grep(self, args: list[str], *, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_grep(self, args, stdin_text=stdin_text)

    async def _cmd_wc(self, args: list[str], *, stdin_text: str | None = None) -> str:
        return await filesystem_commands.cmd_wc(self, args, stdin_text=stdin_text)

    def _ensure_continuous_mode(self) -> None:
        if getattr(self.vfs.thread, "mode", None) != Thread.Mode.CONTINUOUS:
            raise TerminalCommandError("History commands are only available in continuous mode.")

    def _build_continuous_agent_proxy(self):
        return SimpleNamespace(user=self.vfs.user, thread=self.vfs.thread)

    @staticmethod
    def _parse_int_flag(token: str, value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise TerminalCommandError(f"Invalid integer value for {token}: {value}") from exc

    @staticmethod
    def _format_history_search_payload(payload: dict) -> str:
        results = list(payload.get("results") or [])
        notes = list(payload.get("notes") or [])
        if not results:
            if notes:
                return "\n".join(str(note) for note in notes)
            return "No matching history entries found."

        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            kind = str(result.get("kind") or "result")
            if kind == "summary":
                lines.append(
                    f"{index}. summary day={result.get('day_label') or '?'} "
                    f"day_segment_id={result.get('day_segment_id') or '?'}"
                )
                lines.append(str(result.get("summary_snippet") or "").strip() or "(empty summary)")
                continue
            lines.append(
                f"{index}. message day={result.get('day_label') or '?'} "
                f"day_segment_id={result.get('day_segment_id') or '?'} "
                f"message_id={result.get('message_id') or '?'}"
            )
            lines.append(str(result.get("snippet") or "").strip() or "(empty snippet)")
        if notes:
            lines.append("")
            lines.extend(f"Note: {note}" for note in notes)
        return "\n".join(lines).strip()

    @staticmethod
    def _format_history_get_payload(payload: dict) -> str:
        error = str(payload.get("error") or "").strip()
        if error == "not_found":
            return "No matching history entry found."
        if error == "invalid_request":
            raise TerminalCommandError("Usage: history get --day-segment <id> | --message <id> [--limit N] | --from-message <id> --to-message <id> [--limit N]")

        if payload.get("day_segment_id") is not None and "summary_markdown" in payload:
            summary = str(payload.get("summary_markdown") or "").strip()
            return (
                f"day_segment_id={payload.get('day_segment_id')} "
                f"day={payload.get('day_label') or '?'}\n"
                f"{summary or '(empty summary)'}"
            )

        messages = list(payload.get("messages") or [])
        if not messages:
            return "No messages returned."

        lines: list[str] = []
        for item in messages:
            lines.append(
                f"[{item.get('message_id')}] {item.get('role')} @ {item.get('created_at') or '?'}"
            )
            lines.append(str(item.get("content") or "").strip() or "(empty message)")
        if payload.get("truncated"):
            lines.append("")
            lines.append("Note: results were truncated; narrow the range or lower the limit if needed.")
        return "\n".join(lines).strip()

    async def _cmd_history(self, args: list[str]) -> str:
        self._ensure_continuous_mode()
        if not args:
            raise TerminalCommandError("Usage: history search <query> | history get <options>")

        subcommand = str(args[0] or "").strip().lower()
        if subcommand == "search":
            return await self._cmd_history_search(args[1:])
        if subcommand == "get":
            return await self._cmd_history_get(args[1:])
        raise TerminalCommandError("Usage: history search <query> | history get <options>")

    async def _cmd_history_search(self, args: list[str]) -> str:
        if not args:
            raise TerminalCommandError("Usage: history search <query> [--day YYYY-MM-DD] [--recency-days N] [--limit N] [--offset N]")

        query_tokens: list[str] = []
        day = None
        recency_days = 14
        limit = 6
        offset = 0
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--day":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --day")
                day = args[index]
            elif token == "--recency-days":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --recency-days")
                recency_days = self._parse_int_flag("--recency-days", args[index])
            elif token == "--limit":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --limit")
                limit = self._parse_int_flag("--limit", args[index])
            elif token == "--offset":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --offset")
                offset = self._parse_int_flag("--offset", args[index])
            else:
                query_tokens.append(token)
            index += 1

        query = " ".join(query_tokens).strip()
        if not query:
            raise TerminalCommandError("Usage: history search <query> [--day YYYY-MM-DD] [--recency-days N] [--limit N] [--offset N]")

        payload = await conversation_search(
            query=query,
            agent=self._build_continuous_agent_proxy(),
            day=day,
            recency_days=recency_days,
            limit=limit,
            offset=offset,
        )
        return self._format_history_search_payload(payload)

    async def _cmd_history_get(self, args: list[str]) -> str:
        if not args:
            raise TerminalCommandError("Usage: history get --day-segment <id> | --message <id> [--limit N] | --from-message <id> --to-message <id> [--limit N]")

        options: dict[str, int | None] = {
            "message_id": None,
            "day_segment_id": None,
            "from_message_id": None,
            "to_message_id": None,
            "before_message_id": None,
            "after_message_id": None,
        }
        limit = 30
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--limit":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --limit")
                limit = self._parse_int_flag("--limit", args[index])
            elif token in {
                "--message",
                "--day-segment",
                "--from-message",
                "--to-message",
                "--before-message",
                "--after-message",
            }:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {token}")
                target_key = {
                    "--message": "message_id",
                    "--day-segment": "day_segment_id",
                    "--from-message": "from_message_id",
                    "--to-message": "to_message_id",
                    "--before-message": "before_message_id",
                    "--after-message": "after_message_id",
                }[token]
                options[target_key] = self._parse_int_flag(token, args[index])
            else:
                raise TerminalCommandError("Usage: history get --day-segment <id> | --message <id> [--limit N] | --from-message <id> --to-message <id> [--limit N]")
            index += 1

        payload = await conversation_get(
            agent=self._build_continuous_agent_proxy(),
            message_id=options["message_id"],
            day_segment_id=options["day_segment_id"],
            from_message_id=options["from_message_id"],
            to_message_id=options["to_message_id"],
            limit=limit,
            before_message_id=options["before_message_id"],
            after_message_id=options["after_message_id"],
        )
        return self._format_history_get_payload(payload)

    async def _cmd_memory(self, args: list[str]) -> str:
        return await memory_commands.cmd_memory(self, args)

    async def _cmd_mcp(
        self,
        args: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        return await integration_commands.cmd_mcp(
            self,
            args,
            stdin_text=stdin_text,
            capture_output=capture_output,
        )

    async def _cmd_api(
        self,
        args: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        return await integration_commands.cmd_api(
            self,
            args,
            stdin_text=stdin_text,
            capture_output=capture_output,
        )

    @staticmethod
    def _format_webapp_listing(items: list[dict]) -> str:
        if not items:
            return "No webapps are exposed for this conversation."
        lines = ["Exposed webapps:"]
        for item in items:
            status = str(item.get("status") or "unknown").strip()
            lines.append(
                f"- {item.get('name') or item.get('slug')} [{item.get('slug')}] "
                f"{item.get('public_url')} ({status})"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_webapp_details(payload: dict) -> str:
        lines = [
            f"slug={payload.get('slug')}",
            f"name={payload.get('name')}",
            f"source_root={payload.get('source_root')}",
            f"entry_path={payload.get('entry_path')}",
            f"public_url={payload.get('public_url')}",
            f"status={payload.get('status')}",
        ]
        detail = str(payload.get("status_detail") or "").strip()
        if detail:
            lines.append(f"status_detail={detail}")
        return "\n".join(lines)

    async def _cmd_webapp(self, args: list[str]) -> str:
        return await webapp_commands.cmd_webapp(self, args)

    async def _cmd_date(self, args: list[str]) -> str:
        if not self.capabilities.has_date_time:
            raise TerminalCommandError("Date/time commands are not enabled for this agent.")

        use_utc = False
        format_tokens: list[str] = []
        for token in args:
            if token == "-u":
                use_utc = True
                continue
            format_tokens.append(token)
        if format_tokens and not str(format_tokens[0]).startswith("+"):
            raise TerminalCommandError("Usage: date [-u] [+FORMAT [FORMAT ...]]")

        now = timezone.now()
        current = now.astimezone(dt_timezone.utc) if use_utc else timezone.localtime(now)
        if format_tokens:
            format_string = " ".join(str(token)[1:] if str(token).startswith("+") else str(token) for token in format_tokens)
            return current.strftime(format_string)
        zone_label = "UTC" if use_utc else str(current.tzname() or timezone.get_current_timezone_name())
        return f"{current.strftime('%Y-%m-%d %H:%M:%S')} {zone_label}"

    def _parse_output_path(self, args: list[str], *,
                           default_filename: str | None = None) -> tuple[str | None, list[str]]:
        output_path = None
        remaining = []
        index = 0
        while index < len(args):
            token = args[index]
            if token in {"--output", "-o", "-O"}:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {token}")
                output_path = args[index]
            else:
                remaining.append(token)
            index += 1
        if output_path is None and default_filename:
            output_path = posixpath.join(self.vfs.cwd, default_filename)
        return output_path, remaining

    @staticmethod
    def _python_usage() -> str:
        return (
            "Usage: python [--workdir PATH] [--output PATH] <script.py> or "
            'python [--workdir PATH] [--output PATH] -c "..."'
        )

    def _parse_python_command(self, args: list[str]) -> ParsedPythonCommand:
        output_path = None
        workdir = None
        remaining: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token in {"--output", "-o", "-O"}:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {token}")
                output_path = args[index]
            elif token == "--workdir":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --workdir")
                workdir = args[index]
            else:
                remaining.append(token)
            index += 1

        if not remaining:
            raise TerminalCommandError(self._python_usage())

        if remaining[0] == "-c":
            if len(remaining) != 2:
                raise TerminalCommandError(self._python_usage())
            return ParsedPythonCommand(
                output_path=output_path,
                workdir=workdir,
                inline_code=remaining[1],
            )

        if len(remaining) != 1:
            raise TerminalCommandError(self._python_usage())

        return ParsedPythonCommand(
            output_path=output_path,
            workdir=workdir,
            script_path=remaining[0],
        )

    @staticmethod
    def _parse_http_header_value(header_value: str, *, command_name: str) -> tuple[str, str]:
        raw_header = str(header_value or "").strip()
        if ":" not in raw_header:
            raise TerminalCommandError(
                f"Invalid {command_name} header: expected 'Name: value'.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        name, value = raw_header.split(":", 1)
        normalized_name = str(name or "").strip()
        normalized_value = str(value or "").strip()
        if not normalized_name:
            raise TerminalCommandError(
                f"Invalid {command_name} header: missing header name.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        return normalized_name, normalized_value

    @classmethod
    def _parse_download_command(
        cls,
        args: list[str],
        *,
        command_name: str,
        usage: str,
        output_flags: set[str],
        user_agent_flags: set[str],
        header_flags: set[str],
    ) -> ParsedDownloadCommand:
        output_path: str | None = None
        headers: dict[str, str] = {}
        user_agent = ""
        urls: list[str] = []
        index = 0
        end_of_options = False

        while index < len(args):
            token = args[index]
            if not end_of_options and token == "--":
                end_of_options = True
                index += 1
                continue
            if not end_of_options and token in output_flags:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(
                        f"Missing value after {token}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                output_path = args[index]
                index += 1
                continue
            if not end_of_options and token in user_agent_flags:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(
                        f"Missing value after {token}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                user_agent = str(args[index] or "")
                index += 1
                continue
            if not end_of_options and token in header_flags:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(
                        f"Missing value after {token}",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
                header_name, header_value = cls._parse_http_header_value(
                    args[index],
                    command_name=command_name,
                )
                if header_name.lower() == "user-agent":
                    user_agent = header_value
                else:
                    headers[header_name] = header_value
                index += 1
                continue
            if not end_of_options and token.startswith("-"):
                raise TerminalCommandError(
                    f"Unsupported {command_name} flag: {token}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            urls.append(token)
            index += 1

        if not urls:
            raise TerminalCommandError(
                usage,
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if len(urls) > 1:
            raise TerminalCommandError(
                f"{command_name} currently supports a single URL.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        return ParsedDownloadCommand(
            url=urls[0],
            output_path=output_path,
            headers=headers,
            user_agent=user_agent,
        )

    async def _download_http(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        user_agent: str = "",
    ) -> tuple[bytes, str, str]:
        try:
            payload = await download_http_file(
                url,
                headers=headers,
                user_agent=user_agent,
            )
        except ValueError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return payload["content"], payload["mime_type"], payload["filename"]

    async def _write_json_output(self, output_path: str, payload: object):
        try:
            resolved_output = await self.vfs.resolve_output_path(output_path)
            written = await self._write_file_and_notify(
                resolved_output,
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                mime_type="application/json",
            )
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return written

    async def _write_text_output(self, output_path: str, content: str, *, mime_type: str = "text/plain"):
        try:
            resolved_output = await self.vfs.resolve_output_path(output_path)
            written = await self._write_file_and_notify(
                resolved_output,
                str(content or "").encode("utf-8"),
                mime_type=mime_type,
            )
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return written

    @staticmethod
    def _truncate_output(content: str, limit: int = 8000) -> str:
        text = str(content or "")
        return text if len(text) <= limit else f"{text[:limit]}\n...[truncated]"

    @staticmethod
    def _format_remote_service_listing(label: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return f"No {label} are configured for this agent."
        lines = [f"Configured {label}:"]
        for item in items:
            lines.append(
                f"- {item.get('name')} [id={item.get('id')}] {item.get('endpoint') or ''}".rstrip()
            )
        return "\n".join(lines)

    @staticmethod
    def _format_api_operation_listing(items: list[dict[str, Any]]) -> str:
        if not items:
            return "No API operations are configured for this service."
        lines = ["Configured API operations:"]
        for item in items:
            line = (
                f"- {item.get('name')} [{item.get('slug')}] "
                f"{item.get('http_method')} {item.get('path_template')}"
            )
            description = str(item.get("description") or "").strip()
            if description:
                line += f" / {description}"
            lines.append(line)
        return "\n".join(lines)

    def _render_structured_stdout(self, payload: object, *, capture_output: bool) -> str:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        return rendered if capture_output else self._truncate_output(rendered)

    @staticmethod
    def _coerce_inline_value(raw_value: str) -> Any:
        value = str(raw_value or "")
        lowered = value.lower()
        if lowered in {"true", "false", "null"}:
            return json.loads(lowered)
        if value and value[0] in {'{', '[', '"'}:
            try:
                return json.loads(value)
            except ValueError:
                return value
        if re.fullmatch(r"-?\d+", value):
            try:
                return int(value)
            except ValueError:
                return value
        if re.fullmatch(r"-?\d+\.\d+", value):
            try:
                return float(value)
            except ValueError:
                return value
        return value

    def _parse_inline_key_values(self, tokens: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for token in list(tokens or []):
            if "=" not in token:
                raise TerminalCommandError(
                    f"Invalid input token: {token}. Use key=value pairs, --input-file, or JSON stdin."
                )
            key, raw_value = token.split("=", 1)
            key = str(key or "").strip()
            if not key:
                raise TerminalCommandError(f"Invalid input token: {token}")
            payload[key] = self._coerce_inline_value(raw_value)
        return payload

    def _parse_json_text(self, raw_text: str | None, *, source_label: str) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw_text or "").strip())
        except ValueError as exc:
            raise TerminalCommandError(f"Invalid JSON provided via {source_label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise TerminalCommandError(f"JSON input from {source_label} must be an object.")
        return payload

    async def _load_command_json_input(
        self,
        *,
        remaining: list[str],
        stdin_text: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        input_file, reduced = self._parse_flag_value(remaining, "--input-file")
        if input_file:
            try:
                payload_text = await self.vfs.read_text(input_file)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return self._parse_json_text(payload_text, source_label=input_file), reduced
        if stdin_text is not None:
            return self._parse_json_text(stdin_text, source_label="stdin"), reduced
        return self._parse_inline_key_values(reduced), []

    def _resolve_remote_tool(
        self,
        *,
        selector: str | None,
        tools: list,
        noun: str,
        flag_name: str,
    ):
        configured = list(tools or [])
        if not configured:
            raise TerminalCommandError(f"{noun.title()} commands are not enabled for this agent.")
        if selector is None:
            if len(configured) == 1:
                return configured[0]
            raise TerminalCommandError(
                f"Multiple {noun} are configured. Use {flag_name} after running {noun.split()[0]} "
                f"{'servers' if noun.startswith('MCP') else 'services'}."
            )
        normalized = str(selector or "").strip()
        for tool in configured:
            if str(getattr(tool, "id", "")) == normalized or str(getattr(tool, "name", "")) == normalized:
                return tool
        raise TerminalCommandError(f"Unknown {noun}: {normalized}")

    @staticmethod
    def _render_api_call_interactive(result: dict[str, Any]) -> str:
        payload = dict(result.get("payload") or {})
        operation = dict(payload.get("operation") or {})
        response = dict(payload.get("response") or {})
        lines = [
            f"{operation.get('http_method')} {operation.get('slug')} -> {response.get('status_code')}",
            f"content_type={response.get('content_type')}",
        ]
        body_kind = str(result.get("body_kind") or response.get("body_kind") or "")
        if body_kind == "json" and response.get("json") is not None:
            lines.append(json.dumps(response.get("json"), ensure_ascii=False, indent=2))
        elif body_kind == "text" and response.get("text") is not None:
            lines.append(str(response.get("text") or ""))
        else:
            lines.append(
                f"Binary response ({response.get('size')} bytes, filename={response.get('filename')}). "
                "Use --output to save it."
            )
        return "\n".join(lines)

    @staticmethod
    def _render_mcp_call_interactive(result: dict[str, Any]) -> str:
        payload = dict(result.get("payload") or {})
        tool_meta = dict(payload.get("tool") or {})
        rendered = json.dumps(payload.get("result"), ensure_ascii=False, indent=2)
        header = f"MCP {tool_meta.get('name')} returned:"
        return f"{header}\n{rendered}"

    async def _get_browser_session(self) -> BrowserSession:
        if self._browser_session is None:
            self._browser_session = BrowserSession()
        return self._browser_session

    async def close(self) -> None:
        if self._browser_session is not None:
            await self._browser_session.close()
            self._browser_session = None

    @staticmethod
    def _append_warnings(message: str, warnings: tuple[str, ...] | list[str]) -> str:
        extra = [str(item).strip() for item in (warnings or []) if str(item).strip()]
        if not extra:
            return message
        return "\n".join([message, *extra])

    def _format_write_result(self, message: str, written) -> str:
        return self._append_warnings(message, getattr(written, "warnings", ()))

    async def _notify_webapp_paths(
        self,
        paths: list[str],
        *,
        moved_from: str | None = None,
        moved_to: str | None = None,
        deleted_roots: list[str] | None = None,
    ) -> None:
        if not self.capabilities.has_webapp:
            return
        normalized_paths = [
            normalize_vfs_path(path, cwd=self.vfs.cwd)
            for path in paths
            if str(path or "").strip()
        ]
        if not normalized_paths and not moved_from and not moved_to:
            return
        try:
            await webapp_service.maybe_touch_impacted_webapps(
                thread=self.vfs.thread,
                paths=normalized_paths,
                moved_from=normalize_vfs_path(moved_from, cwd=self.vfs.cwd) if moved_from else None,
                moved_to=normalize_vfs_path(moved_to, cwd=self.vfs.cwd) if moved_to else None,
                deleted_roots=[
                    normalize_vfs_path(path, cwd=self.vfs.cwd)
                    for path in list(deleted_roots or [])
                    if str(path or "").strip()
                ],
                task_id=self.realtime_task_id,
                channel_layer=self.realtime_channel_layer,
            )
        except Exception:
            logger.exception(
                "Could not refresh impacted live webapps for thread_id=%s paths=%s moved_from=%s moved_to=%s",
                getattr(self.vfs.thread, "id", None),
                normalized_paths,
                moved_from,
                moved_to,
            )

    async def _write_file_and_notify(self, *args, **kwargs):
        written = await self.vfs.write_file(*args, **kwargs)
        await self._notify_webapp_paths([written.path])
        return written

    async def _mkdir_and_notify(self, path: str) -> str:
        created = await self.vfs.mkdir(path)
        await self._notify_webapp_paths([created])
        return created

    async def _mkdir_recursive_and_notify(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.vfs.cwd)
        if normalized == "/":
            return normalized

        current = "/"
        for segment in [part for part in normalized.strip("/").split("/") if part]:
            current = posixpath.join(current, segment) if current != "/" else f"/{segment}"
            if await self.vfs.is_dir(current):
                continue
            await self._mkdir_and_notify(current)
        return normalized

    async def _copy_and_notify(self, source: str, destination: str):
        copied = await self.vfs.copy(source, destination)
        await self._notify_webapp_paths([copied.path])
        return copied

    async def _move_and_notify(self, source: str, destination: str) -> str:
        normalized_source = normalize_vfs_path(source, cwd=self.vfs.cwd)
        moved = await self.vfs.move(source, destination)
        await self._notify_webapp_paths([normalized_source, moved], moved_from=normalized_source, moved_to=moved)
        return moved

    async def _remove_and_notify(self, path: str, *, recursive: bool = False) -> str:
        normalized = normalize_vfs_path(path, cwd=self.vfs.cwd)
        await self.vfs.remove(path, recursive=recursive)
        await self._notify_webapp_paths(
            [normalized],
            deleted_roots=[normalized] if recursive else None,
        )
        return normalized

    async def _cmd_search(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_search(self, args, capture_output=capture_output)

    async def _cmd_browse(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_browse(self, args, capture_output=capture_output)

    async def _cmd_browse_open(self, args: list[str]) -> str:
        return await web_commands.cmd_browse_open(self, args)

    async def _cmd_browse_current(self, args: list[str]) -> str:
        return await web_commands.cmd_browse_current(self, args)

    async def _cmd_browse_ls(self, args: list[str]) -> str:
        return await web_commands.cmd_browse_ls(self, args)

    async def _cmd_browse_back(self, args: list[str]) -> str:
        return await web_commands.cmd_browse_back(self, args)

    async def _cmd_browse_text(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_browse_text(self, args, capture_output=capture_output)

    async def _cmd_browse_links(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_browse_links(self, args, capture_output=capture_output)

    async def _cmd_browse_elements(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_browse_elements(self, args, capture_output=capture_output)

    async def _cmd_browse_click(self, args: list[str]) -> str:
        return await web_commands.cmd_browse_click(self, args)

    async def _cmd_wget(self, args: list[str]) -> str:
        return await web_commands.cmd_wget(self, args)

    async def _cmd_curl(self, args: list[str], *, capture_output: bool = False) -> str:
        return await web_commands.cmd_curl(self, args, capture_output=capture_output)

    def _parse_flag_value(self, args: list[str], flag: str) -> tuple[str | None, list[str]]:
        remaining: list[str] = []
        value = None
        index = 0
        while index < len(args):
            token = args[index]
            if token == flag:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {flag}")
                value = args[index]
            else:
                remaining.append(token)
            index += 1
        return value, remaining

    def _parse_browser_pane(self, args: list[str]) -> tuple[int | None, list[str]]:
        pane_value, remaining = self._parse_flag_value(args, "--pane")
        if pane_value is None:
            return None, remaining
        pane_index = self._parse_int_flag("--pane", pane_value)
        if pane_index != 0:
            raise TerminalCommandError(BROWSER_SINGLE_PANE_ERROR)
        return pane_index, remaining

    @staticmethod
    def _format_browser_extraction_error(message: str, *, inline_command: str) -> str:
        text = str(message or "").strip()
        if text.startswith("No active page in the current browser session."):
            return (
                "No active page in the current browser session. "
                f"Use `browse open <url>` first or run `{inline_command} <url>` directly."
            )
        return text

    async def _browse_open_inline_url(self, session: BrowserSession, url: str | None) -> None:
        if not str(url or "").strip():
            return
        try:
            await session.open(str(url))
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc

    def _parse_multi_flag(self, args: list[str], flag: str) -> tuple[list[str], list[str]]:
        values: list[str] = []
        remaining: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == flag:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {flag}")
                values.append(args[index])
            else:
                remaining.append(token)
            index += 1
        return values, remaining

    async def _write_calendar_output(self, output_path: str, payload: object, markdown: str) -> str:
        try:
            resolved_output = await self.vfs.resolve_output_path(output_path)
            if resolved_output.endswith(".json"):
                written = await self._write_file_and_notify(
                    resolved_output,
                    json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    mime_type="application/json",
                )
            elif resolved_output.endswith(".md"):
                written = await self._write_file_and_notify(
                    resolved_output,
                    markdown.encode("utf-8"),
                    mime_type="text/markdown",
                )
            else:
                raise TerminalCommandError("Calendar output paths must end with .json or .md")
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return self._format_write_result(f"Wrote calendar output to {written.path}", written)

    async def _get_calendar_registry(self):
        if self._calendar_registry_cache is None:
            agent = SimpleNamespace(user=self.vfs.user, thread=self.vfs.thread)
            self._calendar_registry_cache = await caldav_service.build_calendar_registry(
                list(self.capabilities.caldav_tools or []),
                agent=agent,
            )
        return self._calendar_registry_cache

    async def _resolve_terminal_calendar_account(self, account: str | None):
        _user, entries, lookup, selector_values = await self._get_calendar_registry()
        requested = str(account or "").strip()
        if not requested:
            if len(entries) == 1:
                return entries[0], selector_values
            raise TerminalCommandError(
                "The --account selector is required when multiple calendar accounts are configured. "
                f"Available accounts: {', '.join(selector_values)}."
            )
        entry, err = caldav_service.resolve_calendar_account(requested, lookup, selector_values)
        if err:
            raise TerminalCommandError(err)
        return entry, selector_values

    @staticmethod
    def _format_calendar_accounts(entries: list[dict]) -> str:
        lines = ["Configured calendar accounts:"]
        for entry in entries:
            label = str(entry.get("display_label") or "").strip()
            label_part = f", label: {label}" if label else ""
            lines.append(f"- {entry['account']}{label_part}")
        return "\n".join(lines)

    @staticmethod
    def _format_calendar_event(event: dict, *, index: int | None = None, detailed: bool = False) -> str:
        prefix = f"{index}. " if index is not None else ""
        summary = str(event.get("summary") or "").strip() or "(untitled)"
        uid = str(event.get("uid") or "").strip() or "unknown"
        details = [
            f"uid={uid}",
            f"calendar={event.get('calendar_name') or ''}",
            f"start={event.get('start') or ''}",
        ]
        if event.get("end"):
            details.append(f"end={event['end']}")
        if event.get("all_day"):
            details.append("all_day=yes")
        if event.get("is_recurring"):
            details.append("recurring=yes")
        if event.get("location"):
            details.append(f"location={event['location']}")
        lines = [f"{prefix}{summary} [{uid}]", f"   {' '.join(details)}".rstrip()]
        description = str(event.get("description") or "").strip()
        if detailed and description:
            lines.append(description)
        return "\n".join(lines)

    def _format_calendar_event_list(
        self,
        events: list[dict],
        *,
        heading: str,
        detailed: bool = False,
    ) -> str:
        if not events:
            return "No calendar events found."
        lines = [heading]
        for index, event in enumerate(events, start=1):
            lines.append(self._format_calendar_event(event, index=index, detailed=detailed))
        return "\n".join(lines)

    def _render_calendar_markdown(
        self,
        *,
        heading: str,
        events: list[dict] | None = None,
        event: dict | None = None,
    ) -> str:
        lines = [f"# {heading}"]
        if event is not None:
            lines.append(f"## {event.get('summary') or '(untitled)'}")
            lines.append(f"- UID: `{event.get('uid') or ''}`")
            lines.append(f"- Calendar: {event.get('calendar_name') or ''}")
            lines.append(f"- Start: {event.get('start') or ''}")
            if event.get("end"):
                lines.append(f"- End: {event['end']}")
            lines.append(f"- All day: {'yes' if event.get('all_day') else 'no'}")
            lines.append(f"- Recurring: {'yes' if event.get('is_recurring') else 'no'}")
            if event.get("location"):
                lines.append(f"- Location: {event['location']}")
            description = str(event.get("description") or "").strip()
            if description:
                lines.append("")
                lines.append(description)
            return "\n".join(lines)

        for item in events or []:
            lines.append(f"## {item.get('summary') or '(untitled)'}")
            lines.append(f"- UID: `{item.get('uid') or ''}`")
            lines.append(f"- Calendar: {item.get('calendar_name') or ''}")
            lines.append(f"- Start: {item.get('start') or ''}")
            if item.get("end"):
                lines.append(f"- End: {item['end']}")
            lines.append(f"- All day: {'yes' if item.get('all_day') else 'no'}")
            lines.append(f"- Recurring: {'yes' if item.get('is_recurring') else 'no'}")
            if item.get("location"):
                lines.append(f"- Location: {item['location']}")
            description = str(item.get("description") or "").strip()
            if description:
                lines.append("")
                lines.append(description)
            lines.append("")
        return "\n".join(line for line in lines if line is not None).rstrip()

    async def _cmd_calendar(self, args: list[str]) -> str:
        return await integration_commands.cmd_calendar(self, args)

    async def _get_mailbox_registry(self):
        if self._mailbox_registry_cache is None:
            agent = SimpleNamespace(user=self.vfs.user, thread=self.vfs.thread)
            self._mailbox_registry_cache = await mail_service._build_mailbox_registry(
                list(self.capabilities.email_tools or []),
                agent,
            )
        return self._mailbox_registry_cache

    async def _resolve_terminal_mailbox(self, mailbox: str | None):
        _user, entries, lookup, _mailbox_schema, selector_values = await self._get_mailbox_registry()
        requested = str(mailbox or "").strip()
        if not requested:
            if len(entries) == 1:
                return entries[0], selector_values
            raise TerminalCommandError(
                "The --mailbox selector is required when multiple mailboxes are configured. "
                f"Available addresses: {', '.join(selector_values)}."
            )

        entry, err = mail_service._resolve_mailbox(requested, lookup, selector_values)
        if err:
            raise TerminalCommandError(err)
        return entry, selector_values

    async def _cmd_mail_accounts(self) -> str:
        return await integration_commands.cmd_mail_accounts(self)

    def _parse_mail_single_selector(
        self,
        args: list[str],
        *,
        usage: str,
    ) -> tuple[int | None, int | None]:
        uid_value, remainder = self._parse_flag_value(args, "--uid")
        if uid_value is not None:
            if remainder:
                raise TerminalCommandError(usage)
            return None, self._parse_int_flag("--uid", uid_value)

        if len(remainder) != 1:
            raise TerminalCommandError(usage)
        return self._parse_int_flag("<id>", remainder[0]), None

    def _parse_mail_multi_selectors(
        self,
        args: list[str],
        *,
        usage: str,
    ) -> tuple[list[int], list[int]]:
        uid_values, remainder = self._parse_multi_flag(args, "--uid")
        message_ids: list[int] = []
        for token in remainder:
            if token.startswith("--"):
                raise TerminalCommandError(usage)
            message_ids.append(self._parse_int_flag("<id>", token))
        uids = [self._parse_int_flag("--uid", value) for value in uid_values]
        if not message_ids and not uids:
            raise TerminalCommandError(usage)
        return message_ids, uids

    async def _cmd_mail(self, args: list[str]) -> str:
        return await integration_commands.cmd_mail(self, args)

    async def _send_mail_direct(self, *, tool_id: int, to: str, cc: str | None,
                                subject: str, body: str, attach_paths: list[str]) -> str:
        credential = await mail_service._get_credential(self.vfs.user, tool_id)
        if credential is None:
            raise TerminalCommandError("No email credential found.")

        smtp_server = credential.config.get("smtp_server")
        username = credential.config.get("username")
        from_address = credential.config.get("from_address", username)
        if not smtp_server:
            raise TerminalCommandError("SMTP server not configured.")

        msg = mail_service.MIMEMultipart()
        msg["From"] = from_address
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(mail_service.MIMEText(body, "plain"))

        attachments = []
        for attach_path in attach_paths:
            content, mime_type = await self.vfs.read_bytes(attach_path)
            attachments.append(
                type(
                    "ResolvedAttachment",
                    (),
                    {
                        "filename": posixpath.basename(normalize_vfs_path(attach_path, cwd=self.vfs.cwd)),
                        "mime_type": mime_type,
                        "content": content,
                    },
                )()
            )
        mail_service._attach_binary_parts(msg, attachments)

        server = None
        try:
            server = mail_service.build_smtp_client(credential)
            recipients = [to]
            if cc:
                recipients.extend([item.strip() for item in cc.split(",") if str(item or "").strip()])
            server.sendmail(from_address, recipients, msg.as_string())
        finally:
            mail_service.safe_smtp_quit(server)

        return f"Email sent successfully to {to}"

    @staticmethod
    def _format_python_result_text(
        result: python_service.PythonCommandResult | python_service.PythonExecutionResult,
    ) -> str:
        lines = [f"Status: {result.status_description}"]
        lines.append(f"Stdout: {result.stdout}" if result.stdout else "Stdout: ")
        if result.stderr:
            lines.append(f"Stderr: {result.stderr}")
        return "\n".join(lines).rstrip()

    async def _collect_python_workspace(
        self,
        workdir: str,
    ) -> tuple[list[str], list[python_service.PythonWorkspaceFile]]:
        normalized_workdir = self._validate_python_workspace_root(workdir)
        if not await self.vfs.path_exists(normalized_workdir):
            raise TerminalCommandError(f"Path not found: {normalized_workdir}")
        if not await self.vfs.is_dir(normalized_workdir):
            raise TerminalCommandError(f"Not a directory: {normalized_workdir}")

        directories: set[str] = set()
        files: list[python_service.PythonWorkspaceFile] = []
        prefix = normalized_workdir.rstrip("/") + "/"
        special_roots = {
            "/skills",
            INBOX_ROOT,
            HISTORY_ROOT,
            MEMORY_ROOT,
            "/tmp" if normalized_workdir == "/" else "",
            "/webdav",
        }
        special_prefixes = tuple(f"{root.rstrip('/')}/" for root in special_roots if root)

        for path in sorted(set(await self.vfs.find(normalized_workdir, ""))):
            if path == normalized_workdir:
                continue
            if path in special_roots or any(path.startswith(item) for item in special_prefixes):
                continue
            if not path.startswith(prefix) and normalized_workdir != "/":
                continue

            relative_path = posixpath.relpath(path, normalized_workdir)
            if relative_path.startswith("../"):
                continue
            if await self.vfs.is_dir(path):
                if relative_path != ".":
                    directories.add(relative_path)
                continue
            content, mime_type = await self.vfs.read_bytes(path)
            files.append(
                python_service.PythonWorkspaceFile(
                    path=relative_path,
                    content=content,
                    mime_type=mime_type,
                )
            )

        return sorted(directories), files

    async def _build_python_execution_request(
        self,
        parsed: ParsedPythonCommand,
        *,
        timeout: int,
    ) -> tuple[python_service.PythonExecutionRequest, str | None]:
        if parsed.inline_code is not None:
            if not parsed.workdir:
                return (
                    python_service.PythonExecutionRequest(
                        code=parsed.inline_code,
                        mode="inline",
                        timeout=timeout,
                    ),
                    None,
                )

            workdir = self._validate_python_workspace_root(parsed.workdir)
            directories, files = await self._collect_python_workspace(workdir)
            return (
                python_service.PythonExecutionRequest(
                    code=parsed.inline_code,
                    mode="inline",
                    cwd=".",
                    workspace_directories=tuple(directories),
                    workspace_files=tuple(files),
                    timeout=timeout,
                ),
                workdir,
            )

        script_path = normalize_vfs_path(parsed.script_path or "", cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(script_path):
            raise TerminalCommandError(f"File not found: {script_path}")

        if parsed.workdir:
            workdir = self._validate_python_workspace_root(parsed.workdir)
        else:
            workdir = self._validate_python_workspace_root(posixpath.dirname(script_path) or "/")

        if not await self.vfs.path_exists(workdir):
            raise TerminalCommandError(f"Path not found: {workdir}")
        if not await self.vfs.is_dir(workdir):
            raise TerminalCommandError(f"Not a directory: {workdir}")
        if script_path != workdir and not script_path.startswith(f"{workdir.rstrip('/')}/"):
            raise TerminalCommandError(
                f"Script path {script_path} must be inside the synchronized workspace {workdir}."
            )

        directories, files = await self._collect_python_workspace(workdir)
        return (
            python_service.PythonExecutionRequest(
                mode="script",
                entrypoint=posixpath.relpath(script_path, workdir),
                cwd=".",
                workspace_directories=tuple(directories),
                workspace_files=tuple(files),
                timeout=timeout,
            ),
            workdir,
        )

    async def _apply_python_workspace_writeback(
        self,
        workdir: str,
        result: python_service.PythonExecutionResult,
    ) -> str:
        if not result.output_files:
            return ""

        synced_paths: list[str] = []
        collected_warnings: list[str] = []
        workdir_prefix = workdir.rstrip("/") + "/"
        for output_file in result.output_files:
            destination = normalize_vfs_path(posixpath.join(workdir, output_file.path), cwd="/")
            if destination != workdir and not destination.startswith(workdir_prefix):
                raise TerminalCommandError(f"Invalid Python write-back path: {output_file.path}")
            written = await self._write_file_and_notify(
                destination,
                output_file.content,
                mime_type=output_file.mime_type,
                overwrite=True,
            )
            synced_paths.append(destination)
            collected_warnings.extend(list(getattr(written, "warnings", ()) or ()))

        if len(synced_paths) == 1:
            message = f"Workspace changes synced: {synced_paths[0]}"
        else:
            message = f"Workspace changes synced: {len(synced_paths)} files under {workdir}"
        return self._append_warnings(message, tuple(dict.fromkeys(collected_warnings)))

    async def _cmd_python_result(self, args: list[str]) -> ShellStageResult:
        if not self.capabilities.has_python:
            raise TerminalCommandError("Python execution is not enabled for this agent.")
        if not args:
            raise TerminalCommandError(self._python_usage())

        parsed = self._parse_python_command(args)
        try:
            request, workdir = await self._build_python_execution_request(parsed, timeout=5)
            context_tokens = python_service.push_runtime_context(self.vfs, workdir or self.vfs.cwd)
            try:
                result = await python_service.execute_python_request("", request)
            finally:
                python_service.pop_runtime_context(context_tokens)
        except (ValueError, exec_runner_service.ExecRunnerError) as exc:
            raise TerminalCommandError(str(exc)) from exc

        visible_text = self._format_python_result_text(result)
        if workdir:
            workspace_note = await self._apply_python_workspace_writeback(workdir, result)
            if workspace_note:
                visible_text = f"{visible_text}\n{workspace_note}" if visible_text else workspace_note
        status = 0 if result.ok else 1

        if parsed.output_path:
            output_name = "python-stdout.txt"
            if parsed.script_path:
                script_name = posixpath.basename(normalize_vfs_path(parsed.script_path, cwd=self.vfs.cwd)) or "python"
                stem, _ext = posixpath.splitext(script_name)
                output_name = f"{stem or 'python'}.stdout.txt"
            try:
                resolved_output = await self.vfs.resolve_output_path(
                    self._validate_text_write_path(parsed.output_path),
                    source_name=output_name,
                )
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            written = await self._write_file_and_notify(
                resolved_output,
                result.stdout.encode("utf-8"),
                mime_type="text/plain",
                overwrite=True,
            )
            return ShellStageResult(
                stdout=self._append_warnings(visible_text, written.warnings),
                stderr=result.stderr,
                status=status,
                failure_kind="" if status == 0 else FAILURE_KIND_COMMAND_ERROR,
                status_label=result.status_description,
                display_text=self._append_warnings(visible_text, written.warnings),
            )

        return ShellStageResult(
            stdout=result.stdout,
            stderr=result.stderr,
            status=status,
            failure_kind="" if status == 0 else FAILURE_KIND_COMMAND_ERROR,
            status_label=result.status_description,
            display_text=visible_text,
        )

    async def _cmd_python(self, args: list[str]) -> str:
        result = await self._cmd_python_result(args)
        if result.display_text:
            return result.display_text
        return self._format_python_result_text(
            python_service.PythonCommandResult(
                status_description=result.status_label or ("Accepted" if result.status == 0 else "Error"),
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
