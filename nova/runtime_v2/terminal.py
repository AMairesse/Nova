from __future__ import annotations

import json
import logging
import posixpath
import re
import shlex
from dataclasses import dataclass
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from typing import Any

from django.utils import timezone

from nova.api_tools import service as api_tools_service
from nova.caldav import service as caldav_service
from nova.continuous.tools.conversation_tools import conversation_get, conversation_search
from nova.memory.service import search_memory_items
from nova.mcp import service as mcp_service
from nova.models.Thread import Thread
from nova.runtime_v2.capabilities import TerminalCapabilities
from nova.runtime_v2.vfs import VFSError, VirtualFileSystem, normalize_vfs_path
from nova.tools.builtins import code_execution as code_builtin
from nova.tools.builtins import email as email_builtin
from nova.webapp import service as webapp_service
from nova.web.browser_service import BrowserSession, BrowserSessionError
from nova.web.download_service import download_http_file
from nova.web.search_service import SEARXNG_MAX_RESULTS, search_web

from .constants import RUNTIME_ENGINE_REACT_TERMINAL_V1
from .terminal_metrics import (
    FAILURE_KIND_COMMAND_ERROR,
    FAILURE_KIND_INVALID_ARGUMENTS,
    FAILURE_KIND_PARSE_ERROR,
    FAILURE_KIND_UNSUPPORTED_SYNTAX,
    classify_terminal_failure,
    normalize_head_command,
    record_terminal_command_failure,
    sanitize_terminal_command,
)

class TerminalCommandError(Exception):
    def __init__(self, message: str, *, failure_kind: str | None = None):
        super().__init__(message)
        self.failure_kind = str(failure_kind or "").strip() or classify_terminal_failure(message)


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ParsedShellCommand:
    pipeline: list[list[str]]
    input_path: str | None = None
    output_path: str | None = None
    output_append: bool = False


class TerminalExecutor:
    def __init__(self, *, vfs: VirtualFileSystem, capabilities: TerminalCapabilities):
        self.vfs = vfs
        self.capabilities = capabilities
        self._mailbox_registry_cache = None
        self._calendar_registry_cache = None
        self._last_search_results: list[dict] = []
        self._browser_session: BrowserSession | None = None
        self.realtime_task_id = None
        self.realtime_channel_layer = None

    @property
    def runtime_engine(self) -> str:
        return str(
            getattr(getattr(self.vfs, "agent_config", None), "runtime_engine", "")
            or RUNTIME_ENGINE_REACT_TERMINAL_V1
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

            if raw.startswith("&&", index) or raw.startswith("||", index):
                raise TerminalCommandError(
                    "Shell chaining with && and || is not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
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
            if char == ";":
                raise TerminalCommandError(
                    "Command chaining with ; is not supported.",
                    failure_kind=FAILURE_KIND_UNSUPPORTED_SYNTAX,
                )
            index += 1

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

    def _parse_shell_command(self, command: str) -> ParsedShellCommand:
        tokens = self._tokenize_shell(command)
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
            pipeline=pipeline,
            input_path=input_path,
            output_path=output_path,
            output_append=output_append,
        )

    @staticmethod
    def _command_uses_builtin_output(tokens: list[str]) -> bool:
        return any(token in {"--output", "-o", "-O"} for token in tokens)

    async def _record_terminal_failure(self, command: str, error: TerminalCommandError) -> None:
        failure_kind = str(getattr(error, "failure_kind", "") or classify_terminal_failure(str(error)))
        sanitized_command = sanitize_terminal_command(command)
        payload = {
            "runtime_engine": self.runtime_engine,
            "failure_kind": failure_kind,
            "head_command": normalize_head_command(command),
            "command": sanitized_command,
            "error": str(error),
        }
        logger.warning(
            "terminal_command_failed runtime_engine=%s failure_kind=%s head_command=%s command=%s error=%s",
            payload["runtime_engine"],
            payload["failure_kind"],
            payload["head_command"],
            payload["command"],
            payload["error"],
            extra={"terminal_failure": payload},
        )
        await record_terminal_command_failure(
            runtime_engine=self.runtime_engine,
            command=command,
            failure_kind=failure_kind,
            error_message=str(error),
        )

    async def _run_shell_command(self, parsed: ParsedShellCommand) -> str:
        stdin_text = None
        if parsed.input_path:
            try:
                stdin_text = await self.vfs.read_text(parsed.input_path)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc

        output = ""
        for index, tokens in enumerate(parsed.pipeline):
            capture_output = index < len(parsed.pipeline) - 1 or parsed.output_path is not None
            output = await self._execute_stage(
                tokens,
                stdin_text=stdin_text,
                capture_output=capture_output,
            )
            stdin_text = output

        if parsed.output_path is not None:
            written = await self._write_shell_output(
                parsed.output_path,
                output,
                append=parsed.output_append,
            )
            return self._format_write_result(
                f"Wrote {len(output.encode('utf-8'))} bytes to {written.path}",
                written,
            )
        return output or ""

    async def _execute_stage(
        self,
        tokens: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        if not tokens:
            raise TerminalCommandError(
                "Empty pipeline stage.",
                failure_kind=FAILURE_KIND_PARSE_ERROR,
            )
        name = str(tokens[0] or "").strip()
        args = tokens[1:]
        return await self._dispatch_command(
            name,
            args,
            stdin_text=stdin_text,
            capture_output=capture_output,
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

    async def execute(self, command: str) -> str:
        self.vfs.remember_command(command)
        try:
            parsed = self._parse_shell_command(command)
            return await self._run_shell_command(parsed)
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
    def _parse_ls_flags(args: list[str]) -> tuple[dict[str, bool], str]:
        options = {
            "show_all": False,
            "long_format": False,
            "one_per_line": False,
        }
        path = None
        for token in args:
            if token.startswith("-") and token != "-":
                for flag in token[1:]:
                    if flag == "a":
                        options["show_all"] = True
                    elif flag == "l":
                        options["long_format"] = True
                    elif flag == "1":
                        options["one_per_line"] = True
                    else:
                        raise TerminalCommandError(
                            f"Unsupported ls flag: -{flag}",
                            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                        )
                continue
            if path is not None:
                raise TerminalCommandError(
                    "Usage: ls [-a] [-l] [-1] [path]",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            path = token
        return options, (path or "")

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

    def _format_ls_entry(self, entry: dict[str, str | int], *, long_format: bool) -> str:
        name = str(entry.get("name") or "")
        if entry.get("type") == "dir" and not name.endswith("/"):
            name = f"{name}/"
        if not long_format:
            return name
        size = "-"
        mime_type = "-"
        if entry.get("type") != "dir":
            size = str(entry.get("size") if entry.get("size") is not None else 0)
            mime_type = str(entry.get("mime_type") or "application/octet-stream")
        return f"{self._format_ls_mode(str(entry.get('type') or 'file'))} {size} {mime_type} {name}"

    async def _cmd_ls(self, args: list[str]) -> str:
        options, raw_path = self._parse_ls_flags(args)
        path = raw_path or self.vfs.cwd
        normalized = normalize_vfs_path(path, cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(normalized):
            raise TerminalCommandError(f"Path not found: {normalized}")
        if not await self.vfs.is_dir(normalized):
            entry = await self._lookup_ls_entry(normalized)
            return self._format_ls_entry(entry, long_format=options["long_format"])
        entries = await self.vfs.list_dir(normalized)
        rendered_entries = list(entries)
        if options["show_all"]:
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
            return ""
        lines = [
            self._format_ls_entry(entry, long_format=options["long_format"])
            for entry in rendered_entries
        ]
        return "\n".join(lines)

    async def _cmd_cd(self, args: list[str]) -> str:
        target = args[0] if args else "/"
        normalized = normalize_vfs_path(target, cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(normalized) or not await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Directory not found: {normalized}")
        self.vfs.set_cwd(normalized)
        return self.vfs.cwd

    async def _cmd_cat(self, args: list[str], *, stdin_text: str | None = None) -> str:
        usage = "cat [-n] [<path>]"
        flags, positionals, _numeric_count = self._parse_short_flags(
            args,
            command_name=usage,
            supported_flags={"n"},
        )
        if len(positionals) > 1:
            raise TerminalCommandError(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if not positionals:
            if stdin_text is None:
                raise TerminalCommandError(
                    f"Usage: {usage}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            content = str(stdin_text)
            return self._number_lines(content) if "n" in flags else content
        try:
            content = await self.vfs.read_text(positionals[0])
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return self._number_lines(content) if "n" in flags else content

    async def _cmd_head_tail(self, args: list[str], *, tail: bool, stdin_text: str | None = None) -> str:
        command = "tail" if tail else "head"
        usage = f"{command} [-n N|-N] [<path>]"
        flags, positionals, numeric_count = self._parse_short_flags(
            args,
            command_name=usage,
            supported_flags={"n"},
            allow_numeric_count=True,
        )
        line_count = 10
        if "n" in flags:
            if not positionals:
                raise TerminalCommandError(
                    "Missing value after -n",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            line_count = max(0, self._parse_int_flag("-n", positionals[0]))
            positionals = positionals[1:]
        if numeric_count is not None:
            line_count = max(0, numeric_count)
        if len(positionals) > 1:
            raise TerminalCommandError(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if not positionals:
            if stdin_text is None:
                raise TerminalCommandError(
                    f"Usage: {usage}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            content = str(stdin_text)
        else:
            content = await self._cmd_cat([positionals[0]])
        lines = content.splitlines()
        selected = lines[-line_count:] if tail else lines[:line_count]
        return "\n".join(selected)

    async def _cmd_mkdir(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: mkdir <path>")
        try:
            created = await self._mkdir_and_notify(args[0])
            return f"Created directory {created}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    def _validate_text_write_path(self, raw_path: str) -> str:
        normalized = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
        if normalized.startswith("/skills"):
            raise TerminalCommandError("Writing into /skills is not supported.")
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
        if len(args) != 1:
            raise TerminalCommandError("Usage: touch <path>")
        normalized = self._validate_text_write_path(args[0])
        if await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Cannot touch a directory: {normalized}")
        if await self.vfs.path_exists(normalized):
            return f"Touched {normalized}"
        try:
            written = await self._write_file_and_notify(normalized, b"", mime_type="text/plain")
            return self._format_write_result(f"Created empty file {written.path}", written)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_tee(self, args: list[str], *, stdin_text: str | None = None) -> str:
        if not args:
            raise TerminalCommandError('Usage: tee <path> [--text "<content>"] [--append]')
        append = "--append" in args
        remainder = [item for item in args if item != "--append"]
        text, remainder = self._parse_flag_value(remainder, "--text")
        if len(remainder) != 1:
            raise TerminalCommandError('Usage: tee <path> [--text "<content>"] [--append]')
        if text is not None and stdin_text is not None:
            raise TerminalCommandError(
                "tee cannot combine --text with piped or redirected input.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if text is None and stdin_text is None:
            raise TerminalCommandError('Usage: tee <path> [--text "<content>"] [--append]')

        normalized = self._validate_text_write_path(remainder[0])
        if await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Cannot write text into a directory: {normalized}")

        if text is not None:
            content = self._decode_escaped_text(str(text))
            written = await self._write_shell_output(normalized, content, append=append)
            return self._format_write_result(
                f"Wrote {len(content.encode('utf-8'))} bytes to {written.path}",
                written,
            )

        content = str(stdin_text or "")
        await self._write_shell_output(normalized, content, append=append)
        return content

    async def _cmd_cp(self, args: list[str]) -> str:
        if len(args) != 2:
            raise TerminalCommandError("Usage: cp <source> <destination>")
        try:
            copied = await self._copy_and_notify(args[0], args[1])
            return f"Copied to {copied.path}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_mv(self, args: list[str]) -> str:
        if len(args) != 2:
            raise TerminalCommandError("Usage: mv <source> <destination>")
        try:
            destination = await self._move_and_notify(args[0], args[1])
            return f"Moved to {destination}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_rm(self, args: list[str]) -> str:
        usage = "rm [-f] <path> [<path> ...]"
        flags, positionals, _numeric_count = self._parse_short_flags(
            args,
            command_name=usage,
            supported_flags={"f"},
        )
        if not positionals:
            raise TerminalCommandError(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )

        removed_messages: list[str] = []
        force = "f" in flags
        for path in positionals:
            try:
                removed = await self._remove_and_notify(path)
            except VFSError as exc:
                message = str(exc)
                if force and message.startswith("Path not found:"):
                    continue
                raise TerminalCommandError(message) from exc
            removed_messages.append(f"Removed {removed}")
        return "\n".join(removed_messages)

    async def _cmd_find(self, args: list[str]) -> str:
        start = args[0] if args else self.vfs.cwd
        term = args[1] if len(args) > 1 else ""
        try:
            results = await self.vfs.find(start, term)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return "\n".join(results)

    async def _cmd_grep(self, args: list[str], *, stdin_text: str | None = None) -> str:
        if not args:
            raise TerminalCommandError("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")

        flags, remaining, _numeric_count = self._parse_short_flags(
            args,
            command_name="grep [-r] [-i] [-n] <pattern> [<path>]",
            supported_flags={"r", "i", "n"},
        )
        recursive = "r" in flags
        ignore_case = "i" in flags
        show_numbers = "n" in flags

        if len(remaining) not in {1, 2}:
            raise TerminalCommandError("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")

        pattern = remaining[0]
        candidates: list[str] = []
        stdin_candidate = len(remaining) == 1
        if stdin_candidate:
            if recursive:
                raise TerminalCommandError(
                    "grep -r requires a path.",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            if stdin_text is None:
                raise TerminalCommandError("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")
        else:
            raw_path = remaining[1]
            normalized_path = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
            if not await self.vfs.path_exists(normalized_path):
                raise TerminalCommandError(f"Path not found: {normalized_path}")

            if await self.vfs.is_dir(normalized_path):
                if not recursive:
                    raise TerminalCommandError("grep on directories requires -r")
                try:
                    candidates = await self.vfs.find(normalized_path, "")
                except VFSError as exc:
                    raise TerminalCommandError(str(exc)) from exc
            else:
                candidates = [normalized_path]

        results: list[str] = []
        flags = re.IGNORECASE if ignore_case else 0
        try:
            matcher = re.compile(pattern, flags)
        except re.error as exc:
            raise TerminalCommandError(f"Invalid grep pattern: {exc}") from exc

        if stdin_candidate:
            for line_number, line in enumerate(str(stdin_text or "").splitlines(), start=1):
                if matcher.search(line):
                    prefix = f"stdin:{line_number}:" if show_numbers else ""
                    results.append(f"{prefix}{line}")
            return "\n".join(results)

        for candidate in candidates:
            if await self.vfs.is_dir(candidate):
                continue
            try:
                content = await self.vfs.read_text(candidate)
            except VFSError:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if matcher.search(line):
                    if show_numbers:
                        results.append(f"{candidate}:{line_number}:{line}")
                    else:
                        results.append(f"{candidate}:{line}")
        return "\n".join(results)

    async def _cmd_wc(self, args: list[str], *, stdin_text: str | None = None) -> str:
        usage = "wc -l [<path>]"
        flags, positionals, _numeric_count = self._parse_short_flags(
            args,
            command_name=usage,
            supported_flags={"l"},
        )
        if "l" not in flags or len(positionals) > 1:
            raise TerminalCommandError(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )

        if not positionals:
            if stdin_text is None:
                raise TerminalCommandError(
                    f"Usage: {usage}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            content = str(stdin_text)
            return str(self._count_text_lines(content))

        path = normalize_vfs_path(positionals[0], cwd=self.vfs.cwd)
        content = await self._cmd_cat([positionals[0]])
        return f"{self._count_text_lines(content)} {path}"

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

    @staticmethod
    def _format_memory_search_payload(payload: dict) -> str:
        results = list(payload.get("results") or [])
        notes = list(payload.get("notes") or [])
        if not results:
            if notes:
                return "\n".join(str(note) for note in notes)
            return "No matching memory entries found."

        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            section_heading = str(result.get("section_heading") or "").strip()
            label = str(result.get("path") or "?")
            if section_heading:
                label = f"{label} :: {section_heading}"
            lines.append(f"{index}. {label}")
            lines.append(str(result.get("snippet") or "").strip() or "(empty snippet)")
        if notes:
            lines.append("")
            lines.extend(f"Note: {note}" for note in notes)
        return "\n".join(lines).strip()

    async def _cmd_memory(self, args: list[str]) -> str:
        if not self.capabilities.has_memory:
            raise TerminalCommandError("Memory commands are not enabled for this agent.")
        if not args or str(args[0] or "").strip().lower() != "search":
            raise TerminalCommandError(
                "Usage: memory search <query> [--limit N] [--under /memory/path]"
            )

        query_tokens: list[str] = []
        under = None
        limit = 10
        index = 1
        while index < len(args):
            token = args[index]
            if token == "--limit":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --limit")
                limit = self._parse_int_flag("--limit", args[index])
            elif token == "--under":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --under")
                under = normalize_vfs_path(args[index], cwd=self.vfs.cwd)
            else:
                query_tokens.append(token)
            index += 1

        query = " ".join(query_tokens).strip()
        if not query:
            raise TerminalCommandError(
                "Usage: memory search <query> [--limit N] [--under /memory/path]"
            )

        payload = await search_memory_items(
            query=query,
            user=self.vfs.user,
            limit=limit,
            under=under,
        )
        return self._format_memory_search_payload(payload)

    async def _cmd_mcp(
        self,
        args: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        if not self.capabilities.has_mcp:
            raise TerminalCommandError("MCP commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: mcp <servers|tools|schema|call|refresh> ...")

        subcommand = str(args[0] or "").strip().lower()
        remainder = args[1:]

        if subcommand == "servers":
            if remainder:
                raise TerminalCommandError("Usage: mcp servers")
            payload = [
                {
                    "id": tool.id,
                    "name": tool.name,
                    "endpoint": tool.endpoint,
                    "transport_type": tool.transport_type,
                }
                for tool in self.capabilities.mcp_tools
            ]
            if capture_output:
                return self._render_structured_stdout(payload, capture_output=True)
            return self._format_remote_service_listing("MCP servers", payload)

        if subcommand == "tools":
            server_selector, remainder = self._parse_flag_value(remainder, "--server")
            if remainder:
                raise TerminalCommandError("Usage: mcp tools [--server <selector>]")
            server = self._resolve_remote_tool(
                selector=server_selector,
                tools=self.capabilities.mcp_tools,
                noun="MCP server",
                flag_name="--server",
            )
            try:
                payload = await mcp_service.list_mcp_tools(tool=server, user=self.vfs.user)
            except mcp_service.MCPServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if capture_output:
                return self._render_structured_stdout(payload, capture_output=True)
            if not payload:
                return f"No MCP tools discovered on {server.name}."
            lines = [f"Discovered MCP tools on {server.name}:"]
            for item in payload:
                line = f"- {item.get('name')}"
                description = str(item.get("description") or "").strip()
                if description:
                    line += f" / {description}"
                lines.append(line)
            return "\n".join(lines)

        if subcommand == "schema":
            server_selector, remainder = self._parse_flag_value(remainder, "--server")
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: mcp schema <tool-name> [--server <selector>]")
            server = self._resolve_remote_tool(
                selector=server_selector,
                tools=self.capabilities.mcp_tools,
                noun="MCP server",
                flag_name="--server",
            )
            try:
                payload = await mcp_service.describe_mcp_tool(
                    tool=server,
                    user=self.vfs.user,
                    tool_name=remainder[0],
                )
            except mcp_service.MCPServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return self._render_structured_stdout(payload, capture_output=capture_output)

        if subcommand == "refresh":
            server_selector, remainder = self._parse_flag_value(remainder, "--server")
            if remainder:
                raise TerminalCommandError("Usage: mcp refresh [--server <selector>]")
            servers = (
                [self._resolve_remote_tool(
                    selector=server_selector,
                    tools=self.capabilities.mcp_tools,
                    noun="MCP server",
                    flag_name="--server",
                )]
                if server_selector is not None
                else list(self.capabilities.mcp_tools)
            )
            payload: list[dict[str, Any]] = []
            for server in servers:
                try:
                    tools = await mcp_service.list_mcp_tools(
                        tool=server,
                        user=self.vfs.user,
                        force_refresh=True,
                    )
                except mcp_service.MCPServiceError as exc:
                    raise TerminalCommandError(str(exc)) from exc
                payload.append({"id": server.id, "name": server.name, "tool_count": len(tools)})
            if capture_output:
                return self._render_structured_stdout(payload, capture_output=True)
            if len(payload) == 1:
                entry = payload[0]
                return f"Refreshed {entry['name']} ({entry['tool_count']} tools)."
            return "\n".join(
                [f"Refreshed {entry['name']} ({entry['tool_count']} tools)." for entry in payload]
            )

        if subcommand == "call":
            server_selector, remainder = self._parse_flag_value(remainder, "--server")
            output_path, remainder = self._parse_output_path(remainder)
            extract_to, remainder = self._parse_flag_value(remainder, "--extract-to")
            if not remainder:
                raise TerminalCommandError(
                    "Usage: mcp call <tool-name> [--server <selector>] [--input-file /path.json] "
                    "[--output /path.json] [--extract-to /dir]"
                )
            server = self._resolve_remote_tool(
                selector=server_selector,
                tools=self.capabilities.mcp_tools,
                noun="MCP server",
                flag_name="--server",
            )
            tool_name = remainder[0]
            inline_tokens = remainder[1:]
            payload, leftover = await self._load_command_json_input(
                remaining=inline_tokens,
                stdin_text=stdin_text,
            )
            if leftover:
                raise TerminalCommandError("Unexpected arguments after MCP input payload.")

            try:
                result = await mcp_service.call_mcp_tool(
                    tool=server,
                    user=self.vfs.user,
                    tool_name=tool_name,
                    payload=payload,
                )
            except mcp_service.MCPServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            artifacts = list(result.get("extractable_artifacts") or [])
            if artifacts and not output_path and not extract_to:
                raise TerminalCommandError(
                    "This MCP result includes extractable files or resources. Use --output or --extract-to."
                )

            extracted_paths: list[str] = []
            if extract_to:
                try:
                    await self._mkdir_and_notify(extract_to)
                except VFSError:
                    # Directory may already exist or be a special mount; resolve on writes below.
                    pass
                for artifact in artifacts:
                    destination = posixpath.join(extract_to, artifact.path)
                    written = await self._write_file_and_notify(
                        destination,
                        artifact.content,
                        mime_type=artifact.mime_type,
                    )
                    extracted_paths.append(written.path)

            if output_path:
                written = await self._write_json_output(output_path, result["payload"])
                message = self._format_write_result(f"Wrote MCP result to {written.path}", written)
                if extracted_paths:
                    message += "\nExtracted:\n" + "\n".join(f"- {path}" for path in extracted_paths)
                return message

            if capture_output:
                return self._render_structured_stdout(result["payload"], capture_output=True)

            message = self._truncate_output(self._render_mcp_call_interactive(result))
            if extracted_paths:
                message += "\nExtracted:\n" + "\n".join(f"- {path}" for path in extracted_paths)
            return message

        raise TerminalCommandError("Usage: mcp <servers|tools|schema|call|refresh> ...")

    async def _cmd_api(
        self,
        args: list[str],
        *,
        stdin_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        if not self.capabilities.has_api:
            raise TerminalCommandError("API commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: api <services|operations|schema|call> ...")

        subcommand = str(args[0] or "").strip().lower()
        remainder = args[1:]

        if subcommand == "services":
            if remainder:
                raise TerminalCommandError("Usage: api services")
            payload = [
                {
                    "id": tool.id,
                    "name": tool.name,
                    "endpoint": tool.endpoint,
                }
                for tool in self.capabilities.api_tools
            ]
            if capture_output:
                return self._render_structured_stdout(payload, capture_output=True)
            return self._format_remote_service_listing("API services", payload)

        if subcommand == "operations":
            service_selector, remainder = self._parse_flag_value(remainder, "--service")
            if remainder:
                raise TerminalCommandError("Usage: api operations [--service <selector>]")
            service = self._resolve_remote_tool(
                selector=service_selector,
                tools=self.capabilities.api_tools,
                noun="API service",
                flag_name="--service",
            )
            try:
                payload = await api_tools_service.list_api_operations(tool=service)
            except api_tools_service.APIServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if capture_output:
                return self._render_structured_stdout(payload, capture_output=True)
            return self._format_api_operation_listing(payload)

        if subcommand == "schema":
            service_selector, remainder = self._parse_flag_value(remainder, "--service")
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: api schema <operation> [--service <selector>]")
            service = self._resolve_remote_tool(
                selector=service_selector,
                tools=self.capabilities.api_tools,
                noun="API service",
                flag_name="--service",
            )
            try:
                payload = await api_tools_service.describe_api_operation(
                    tool=service,
                    operation_selector=remainder[0],
                )
            except api_tools_service.APIServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return self._render_structured_stdout(payload, capture_output=capture_output)

        if subcommand == "call":
            service_selector, remainder = self._parse_flag_value(remainder, "--service")
            output_path, remainder = self._parse_output_path(remainder)
            if not remainder:
                raise TerminalCommandError(
                    "Usage: api call <operation> [--service <selector>] [--input-file /path.json] "
                    "[--output /path.json|/path.txt|/path.bin]"
                )
            service = self._resolve_remote_tool(
                selector=service_selector,
                tools=self.capabilities.api_tools,
                noun="API service",
                flag_name="--service",
            )
            operation_selector = remainder[0]
            inline_tokens = remainder[1:]
            payload, leftover = await self._load_command_json_input(
                remaining=inline_tokens,
                stdin_text=stdin_text,
            )
            if leftover:
                raise TerminalCommandError("Unexpected arguments after API input payload.")

            try:
                result = await api_tools_service.call_api_operation(
                    tool=service,
                    user=self.vfs.user,
                    operation_selector=operation_selector,
                    payload=payload,
                )
            except api_tools_service.APIServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc

            if output_path:
                if result["body_kind"] == "binary":
                    try:
                        resolved_output = await self.vfs.resolve_output_path(
                            output_path,
                            source_name=str(result.get("filename") or "response.bin"),
                        )
                    except VFSError as exc:
                        raise TerminalCommandError(str(exc)) from exc
                    written = await self._write_file_and_notify(
                        resolved_output,
                        result["binary_content"],
                        mime_type=result["content_type"],
                    )
                    return self._format_write_result(f"Wrote API response to {written.path}", written)
                if result["body_kind"] == "json" and result["payload"]["response"].get("json") is not None:
                    written = await self._write_json_output(output_path, result["payload"]["response"]["json"])
                    return self._format_write_result(f"Wrote API response to {written.path}", written)
                written = await self._write_text_output(
                    output_path,
                    str(result["payload"]["response"].get("text") or ""),
                    mime_type="text/plain",
                )
                return self._format_write_result(f"Wrote API response to {written.path}", written)

            if result["body_kind"] == "binary":
                if capture_output:
                    raise TerminalCommandError(
                        "Binary API responses cannot be piped or redirected without --output."
                    )
                return (
                    f"Binary API response ({result['content_type']}, {len(result['binary_content'])} bytes). "
                    "Use --output to save it."
                )

            if capture_output:
                return self._render_structured_stdout(result["payload"], capture_output=True)
            return self._truncate_output(self._render_api_call_interactive(result))

        raise TerminalCommandError("Usage: api <services|operations|schema|call> ...")

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
        if not self.capabilities.has_webapp:
            raise TerminalCommandError("Webapp commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: webapp <list|expose|show|delete> ...")

        subcommand = str(args[0] or "").strip().lower()
        remainder = args[1:]

        if subcommand == "list":
            if remainder:
                raise TerminalCommandError("Usage: webapp list")
            items = await webapp_service.list_thread_webapps(user=self.vfs.user, thread=self.vfs.thread)
            return self._format_webapp_listing(items)

        if subcommand == "show":
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: webapp show <slug>")
            try:
                payload = await webapp_service.describe_webapp(
                    user=self.vfs.user,
                    thread=self.vfs.thread,
                    slug=remainder[0],
                )
            except webapp_service.WebAppServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return self._format_webapp_details(payload)

        if subcommand == "delete":
            confirm = "--confirm" in remainder
            remainder = [item for item in remainder if item != "--confirm"]
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: webapp delete <slug> --confirm")
            if not confirm:
                raise TerminalCommandError("webapp delete requires --confirm")
            try:
                payload = await webapp_service.delete_webapp(
                    user=self.vfs.user,
                    thread=self.vfs.thread,
                    slug=remainder[0],
                    task_id=self.realtime_task_id,
                    channel_layer=self.realtime_channel_layer,
                )
            except webapp_service.WebAppServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return f"Deleted webapp {payload['slug']}"

        if subcommand == "expose":
            slug, remainder = self._parse_flag_value(remainder, "--slug")
            name, remainder = self._parse_flag_value(remainder, "--name")
            entry_path, remainder = self._parse_flag_value(remainder, "--entry")
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: webapp expose <source_dir> [--name <display-name>] [--entry <relative-path>] "
                    "[--slug <slug>]"
                )
            try:
                payload = await webapp_service.expose_webapp(
                    user=self.vfs.user,
                    thread=self.vfs.thread,
                    vfs=self.vfs,
                    source_root=remainder[0],
                    slug=slug,
                    name=name,
                    entry_path=entry_path,
                    task_id=self.realtime_task_id,
                    channel_layer=self.realtime_channel_layer,
                )
            except webapp_service.WebAppServiceError as exc:
                raise TerminalCommandError(str(exc)) from exc
            action = "Exposed" if payload.get("created") else "Updated"
            return (
                f"{action} webapp {payload['slug']} at {payload['public_url']} "
                f"from {payload['source_root']} (entry {payload['entry_path']})"
            )

        raise TerminalCommandError("Usage: webapp <list|expose|show|delete> ...")

    async def _cmd_date(self, args: list[str]) -> str:
        if not self.capabilities.has_date_time:
            raise TerminalCommandError("Date/time commands are not enabled for this agent.")

        use_utc = False
        format_token = None
        for token in args:
            if token == "-u":
                use_utc = True
                continue
            if token in {"+%F", "+%T"} and format_token is None:
                format_token = token
                continue
            raise TerminalCommandError("Usage: date [-u] [+%F|+%T]")

        now = timezone.now()
        current = now.astimezone(dt_timezone.utc) if use_utc else timezone.localtime(now)
        if format_token == "+%F":
            return current.strftime("%Y-%m-%d")
        if format_token == "+%T":
            return current.strftime("%H:%M:%S")
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

    async def _download_http(self, url: str) -> tuple[bytes, str, str]:
        try:
            payload = await download_http_file(url)
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

    async def _copy_and_notify(self, source: str, destination: str):
        copied = await self.vfs.copy(source, destination)
        await self._notify_webapp_paths([copied.path])
        return copied

    async def _move_and_notify(self, source: str, destination: str) -> str:
        normalized_source = normalize_vfs_path(source, cwd=self.vfs.cwd)
        moved = await self.vfs.move(source, destination)
        await self._notify_webapp_paths([normalized_source, moved], moved_from=normalized_source, moved_to=moved)
        return moved

    async def _remove_and_notify(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.vfs.cwd)
        await self.vfs.remove(path)
        await self._notify_webapp_paths([normalized])
        return normalized

    async def _cmd_search(self, args: list[str], *, capture_output: bool = False) -> str:
        if not self.capabilities.has_search:
            raise TerminalCommandError("Search commands are not enabled for this agent.")

        output_path, remaining = self._parse_output_path(args)
        limit = None
        query_tokens: list[str] = []
        index = 0
        while index < len(remaining):
            token = remaining[index]
            if token == "--limit":
                index += 1
                if index >= len(remaining):
                    raise TerminalCommandError("Missing value after --limit")
                limit = self._parse_int_flag("--limit", remaining[index])
            else:
                query_tokens.append(token)
            index += 1

        query = " ".join(query_tokens).strip()
        if not query:
            raise TerminalCommandError("Usage: search <query> [--limit N] [--output /path.json]")

        try:
            effective_limit = None if limit is None else max(1, min(limit, SEARXNG_MAX_RESULTS))
            payload = await search_web(self.capabilities.searxng_tool, query=query, limit=effective_limit)
        except Exception as exc:
            raise TerminalCommandError(str(exc)) from exc

        self._last_search_results = list(payload.get("results") or [])

        if output_path:
            written = await self._write_json_output(output_path, payload)
            return self._format_write_result(f"Wrote search results to {written.path}", written)
        if capture_output:
            return self._render_structured_stdout(payload, capture_output=True)

        lines: list[str] = []
        for index, result in enumerate(self._last_search_results, start=1):
            title = str(result.get("title") or "").strip() or "(untitled)"
            url = str(result.get("url") or "").strip() or "(missing url)"
            snippet = str(result.get("snippet") or "").strip()
            line = f"{index}. {title} / {url}"
            if snippet:
                line += f" / {snippet}"
            lines.append(line)
        return "\n".join(lines) if lines else "No search results."

    async def _cmd_browse(self, args: list[str], *, capture_output: bool = False) -> str:
        if not self.capabilities.has_web:
            raise TerminalCommandError("Browse commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError(
                "Usage: browse <open|current|back|text|links|elements|click> ..."
            )
        action = args[0]
        remainder = args[1:]
        if action == "open":
            return await self._cmd_browse_open(remainder)
        if action == "current":
            return await self._cmd_browse_current(remainder)
        if action == "back":
            return await self._cmd_browse_back(remainder)
        if action == "text":
            return await self._cmd_browse_text(remainder, capture_output=capture_output)
        if action == "links":
            return await self._cmd_browse_links(remainder, capture_output=capture_output)
        if action == "elements":
            return await self._cmd_browse_elements(remainder, capture_output=capture_output)
        if action == "click":
            return await self._cmd_browse_click(remainder)
        raise TerminalCommandError(
            "Usage: browse <open|current|back|text|links|elements|click> ..."
        )

    async def _cmd_browse_open(self, args: list[str]) -> str:
        if not args:
            raise TerminalCommandError("Usage: browse open <url> | browse open --result N")

        result_index = None
        remaining: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--result":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after --result")
                result_index = self._parse_int_flag("--result", args[index])
            else:
                remaining.append(token)
            index += 1

        session = await self._get_browser_session()
        try:
            if result_index is not None:
                if remaining:
                    raise TerminalCommandError("Usage: browse open <url> | browse open --result N")
                if not self._last_search_results:
                    raise TerminalCommandError(
                        "No cached search results are available in this run. Use `search` first."
                    )
                opened = await session.open_search_result(result_index, self._last_search_results)
            else:
                if len(remaining) != 1:
                    raise TerminalCommandError("Usage: browse open <url> | browse open --result N")
                opened = await session.open(remaining[0])
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc

        status = opened.get("status")
        url = str(opened.get("url") or "")
        if status is None:
            return f"Opened {url}"
        return f"Opened {url} (status {status})"

    async def _cmd_browse_current(self, args: list[str]) -> str:
        if args:
            raise TerminalCommandError("Usage: browse current")
        session = await self._get_browser_session()
        try:
            return await session.current()
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_browse_back(self, args: list[str]) -> str:
        if args:
            raise TerminalCommandError("Usage: browse back")
        session = await self._get_browser_session()
        try:
            payload = await session.back()
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return f"Went back to {payload['url']} (status {payload['status']})"

    async def _cmd_browse_text(self, args: list[str], *, capture_output: bool = False) -> str:
        output_path, remaining = self._parse_output_path(args)
        if remaining:
            raise TerminalCommandError("Usage: browse text [--output /path.txt]")
        session = await self._get_browser_session()
        try:
            content = await session.extract_text()
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc
        if output_path:
            try:
                resolved_output = await self.vfs.resolve_output_path(output_path)
                written = await self._write_file_and_notify(
                    resolved_output,
                    content.encode("utf-8"),
                    mime_type="text/plain",
                )
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return self._format_write_result(f"Wrote page text to {written.path}", written)
        return content if capture_output else self._truncate_output(content)

    async def _cmd_browse_links(self, args: list[str], *, capture_output: bool = False) -> str:
        output_path, remaining = self._parse_output_path(args)
        absolute = False
        for token in remaining:
            if token == "--absolute":
                absolute = True
            else:
                raise TerminalCommandError("Usage: browse links [--absolute] [--output /path.json]")
        session = await self._get_browser_session()
        try:
            links = await session.extract_links(absolute=absolute)
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc
        if output_path:
            written = await self._write_json_output(output_path, links)
            return self._format_write_result(f"Wrote page links to {written.path}", written)
        rendered = json.dumps(links, ensure_ascii=False, indent=2)
        return rendered if capture_output else self._truncate_output(rendered)

    async def _cmd_browse_elements(self, args: list[str], *, capture_output: bool = False) -> str:
        output_path, remaining = self._parse_output_path(args)
        attributes, remaining = self._parse_multi_flag(remaining, "--attr")
        if len(remaining) != 1:
            raise TerminalCommandError(
                "Usage: browse elements <selector> [--attr NAME ...] [--output /path.json]"
            )
        selector = remaining[0]
        attrs = attributes or ["innerText"]
        session = await self._get_browser_session()
        try:
            elements = await session.get_elements(selector, attrs)
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc
        payload = {
            "selector": selector,
            "attributes": attrs,
            "elements": elements,
        }
        if output_path:
            written = await self._write_json_output(output_path, payload)
            return self._format_write_result(f"Wrote selected elements to {written.path}", written)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        return rendered if capture_output else self._truncate_output(rendered)

    async def _cmd_browse_click(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: browse click <selector>")
        session = await self._get_browser_session()
        try:
            return await session.click(args[0])
        except BrowserSessionError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_wget(self, args: list[str]) -> str:
        if not self.capabilities.has_web:
            raise TerminalCommandError("Web commands are not enabled for this agent.")
        output_path, remaining = self._parse_output_path(args)
        if len(remaining) != 1:
            raise TerminalCommandError("Usage: wget <url> [--output <path>]")
        url = remaining[0]
        content, mime_type, inferred_name = await self._download_http(url)
        destination = output_path or posixpath.join(self.vfs.cwd, inferred_name)
        try:
            destination = await self.vfs.resolve_output_path(destination, source_name=inferred_name)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        written = await self._write_file_and_notify(destination, content, mime_type=mime_type)
        return self._format_write_result(f"Downloaded {url} to {written.path}", written)

    async def _cmd_curl(self, args: list[str], *, capture_output: bool = False) -> str:
        if not self.capabilities.has_web:
            raise TerminalCommandError("Web commands are not enabled for this agent.")
        output_path, remaining = self._parse_output_path(args)
        if len(remaining) != 1:
            raise TerminalCommandError("Usage: curl <url> [--output <path>]")
        url = remaining[0]
        content, mime_type, inferred_name = await self._download_http(url)
        if output_path:
            try:
                resolved_output = await self.vfs.resolve_output_path(output_path, source_name=inferred_name)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            written = await self._write_file_and_notify(resolved_output, content, mime_type=mime_type)
            return self._format_write_result(f"Downloaded {url} to {written.path}", written)
        if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
            try:
                decoded = content.decode("utf-8")
                return decoded if capture_output else decoded[:8000]
            except UnicodeDecodeError:
                if capture_output:
                    raise TerminalCommandError(
                        "curl only supports text output when used in a pipeline or shell redirection. "
                        "Use curl --output <path> to save binary responses.",
                        failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                    )
        if capture_output:
            raise TerminalCommandError(
                "curl only supports text output when used in a pipeline or shell redirection. "
                "Use curl --output <path> to save binary responses.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        return (
            f"Binary response from {url} ({mime_type}, {len(content)} bytes). "
            f"Use curl --output {posixpath.join(self.vfs.cwd, inferred_name)} to save it."
        )

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
        if not self.capabilities.has_calendar:
            raise TerminalCommandError("Calendar commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError(
                "Usage: calendar <accounts|calendars|upcoming|list|search|show|create|update|delete> ..."
            )
        subcommand = args[0]
        remainder = args[1:]
        account, remainder = self._parse_flag_value(remainder, "--account")

        if subcommand == "accounts":
            if remainder:
                raise TerminalCommandError("Usage: calendar accounts")
            _user, entries, _lookup, selector_values = await self._get_calendar_registry()
            lines = [self._format_calendar_accounts(entries)]
            if len(selector_values) > 1:
                lines.append("Pass --account <selector> on calendar commands to choose an account explicitly.")
            return "\n".join(lines)

        entry, _selector_values = await self._resolve_terminal_calendar_account(account)
        tool_id = int(entry["tool_id"])

        if subcommand == "calendars":
            if remainder:
                raise TerminalCommandError("Usage: calendar calendars [--account <selector>]")
            calendars = await caldav_service.list_calendars(self.vfs.user, tool_id)
            if not calendars:
                return "No calendars available."
            return "\n".join(["Available calendars:", *[f"- {item}" for item in calendars]])

        if subcommand == "upcoming":
            output_path, remainder = self._parse_output_path(remainder)
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            days_value, remainder = self._parse_flag_value(remainder, "--days")
            if remainder:
                raise TerminalCommandError(
                    "Usage: calendar upcoming [--account <selector>] [--calendar <name>] [--days N] [--output /path.md|json]"
                )
            days = self._parse_int_flag("--days", days_value or "7")
            try:
                events = await caldav_service.list_events_to_come(
                    self.vfs.user,
                    tool_id,
                    days_ahead=days,
                    calendar_name=calendar_name,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if output_path:
                return await self._write_calendar_output(
                    output_path,
                    {"events": events, "days": days, "account": entry["account"], "calendar": calendar_name},
                    self._render_calendar_markdown(heading="Upcoming Events", events=events),
                )
            return self._format_calendar_event_list(events, heading="Upcoming events:")

        if subcommand == "list":
            output_path, remainder = self._parse_output_path(remainder)
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            start_value, remainder = self._parse_flag_value(remainder, "--from")
            end_value, remainder = self._parse_flag_value(remainder, "--to")
            if remainder or not start_value or not end_value:
                raise TerminalCommandError(
                    "Usage: calendar list --from <iso> --to <iso> [--account <selector>] [--calendar <name>] [--output /path.md|json]"
                )
            try:
                events = await caldav_service.list_events(
                    self.vfs.user,
                    tool_id,
                    start_date=start_value,
                    end_date=end_value,
                    calendar_name=calendar_name,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if output_path:
                return await self._write_calendar_output(
                    output_path,
                    {"events": events, "from": start_value, "to": end_value, "account": entry["account"], "calendar": calendar_name},
                    self._render_calendar_markdown(heading="Calendar Events", events=events),
                )
            return self._format_calendar_event_list(events, heading="Calendar events:")

        if subcommand == "search":
            output_path, remainder = self._parse_output_path(remainder)
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            days_value, remainder = self._parse_flag_value(remainder, "--days")
            query = " ".join(remainder).strip()
            if not query:
                raise TerminalCommandError(
                    "Usage: calendar search <query> [--account <selector>] [--calendar <name>] [--days N] [--output /path.md|json]"
                )
            days = self._parse_int_flag("--days", days_value or "30")
            try:
                events = await caldav_service.search_events(
                    self.vfs.user,
                    tool_id,
                    query=query,
                    days_range=days,
                    calendar_name=calendar_name,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if output_path:
                return await self._write_calendar_output(
                    output_path,
                    {"events": events, "query": query, "days": days, "account": entry["account"], "calendar": calendar_name},
                    self._render_calendar_markdown(heading=f"Calendar Search: {query}", events=events),
                )
            return self._format_calendar_event_list(events, heading="Matching calendar events:")

        if subcommand == "show":
            output_path, remainder = self._parse_output_path(remainder)
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: calendar show <event-id> [--account <selector>] [--calendar <name>] [--output /path.md|json]"
                )
            try:
                event = await caldav_service.get_event_detail(
                    self.vfs.user,
                    tool_id,
                    event_id=remainder[0],
                    calendar_name=calendar_name,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            if output_path:
                return await self._write_calendar_output(
                    output_path,
                    {"event": event, "account": entry["account"], "calendar": calendar_name},
                    self._render_calendar_markdown(heading="Calendar Event", event=event),
                )
            return self._format_calendar_event(event, detailed=True)

        if subcommand == "create":
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            title, remainder = self._parse_flag_value(remainder, "--title")
            start_value, remainder = self._parse_flag_value(remainder, "--start")
            end_value, remainder = self._parse_flag_value(remainder, "--end")
            location, remainder = self._parse_flag_value(remainder, "--location")
            description_file, remainder = self._parse_flag_value(remainder, "--description-file")
            all_day = "--all-day" in remainder
            remainder = [item for item in remainder if item != "--all-day"]
            if remainder or not calendar_name or not title or not start_value:
                raise TerminalCommandError(
                    "Usage: calendar create --title <text> --start <iso> [--end <iso>] [--all-day] --calendar <name> [--account <selector>] [--location <text>] [--description-file /path.md]"
                )
            description = await self.vfs.read_text(description_file) if description_file else None
            try:
                event = await caldav_service.create_event(
                    self.vfs.user,
                    tool_id,
                    calendar_name=calendar_name,
                    summary=title,
                    start=start_value,
                    end=end_value,
                    all_day=all_day,
                    location=location,
                    description=description,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return f"Created event {event['uid']} in calendar {event['calendar_name']}"

        if subcommand == "update":
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            title, remainder = self._parse_flag_value(remainder, "--title")
            start_value, remainder = self._parse_flag_value(remainder, "--start")
            end_value, remainder = self._parse_flag_value(remainder, "--end")
            location, remainder = self._parse_flag_value(remainder, "--location")
            description_file, remainder = self._parse_flag_value(remainder, "--description-file")
            all_day = "--all-day" in remainder
            remainder = [item for item in remainder if item != "--all-day"]
            if len(remainder) != 1 or not calendar_name:
                raise TerminalCommandError(
                    "Usage: calendar update <event-id> [--account <selector>] --calendar <name> [--title <text>] [--start <iso>] [--end <iso>] [--all-day] [--location <text>] [--description-file /path.md]"
                )
            description = await self.vfs.read_text(description_file) if description_file else None
            if not any(
                value is not None
                for value in [title, start_value, end_value, location, description]
            ) and not all_day:
                raise TerminalCommandError("calendar update requires at least one field to change.")
            try:
                event = await caldav_service.update_event(
                    self.vfs.user,
                    tool_id,
                    event_id=remainder[0],
                    calendar_name=calendar_name,
                    summary=title,
                    start=start_value,
                    end=end_value,
                    all_day=True if all_day else None,
                    location=location,
                    description=description,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return f"Updated event {event['uid']} in calendar {event['calendar_name']}"

        if subcommand == "delete":
            calendar_name, remainder = self._parse_flag_value(remainder, "--calendar")
            confirm = "--confirm" in remainder
            remainder = [item for item in remainder if item != "--confirm"]
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: calendar delete <event-id> [--account <selector>] [--calendar <name>] --confirm"
                )
            if not confirm:
                raise TerminalCommandError("calendar delete requires --confirm")
            try:
                event = await caldav_service.delete_event(
                    self.vfs.user,
                    tool_id,
                    event_id=remainder[0],
                    calendar_name=calendar_name,
                )
            except ValueError as exc:
                raise TerminalCommandError(str(exc)) from exc
            return f"Deleted event {event['uid']} from calendar {event['calendar_name']}"

        raise TerminalCommandError(
            "Usage: calendar <accounts|calendars|upcoming|list|search|show|create|update|delete> ..."
        )

    async def _get_mailbox_registry(self):
        if self._mailbox_registry_cache is None:
            agent = SimpleNamespace(user=self.vfs.user, thread=self.vfs.thread)
            self._mailbox_registry_cache = await email_builtin._build_mailbox_registry(
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

        entry, err = email_builtin._resolve_mailbox(requested, lookup, selector_values)
        if err:
            raise TerminalCommandError(err)
        return entry, selector_values

    async def _cmd_mail_accounts(self) -> str:
        _user, entries, _lookup, _mailbox_schema, selector_values = await self._get_mailbox_registry()
        if not entries:
            raise TerminalCommandError("No email mailbox is configured for this agent.")
        lines = ["Configured mailboxes:"]
        for entry in entries:
            label = str(entry.get("display_label") or "").strip()
            label_part = f", label: {label}" if label else ""
            sending = "enabled" if entry.get("can_send") else "disabled"
            lines.append(
                f"- {entry['selector_email']} (sending: {sending}{label_part})"
            )
        if len(selector_values) > 1:
            lines.append("Pass --mailbox <email> on mail commands to choose an account explicitly.")
        return "\n".join(lines)

    async def _cmd_mail(self, args: list[str]) -> str:
        if not self.capabilities.has_email:
            raise TerminalCommandError("Mail commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: mail <accounts|list|read|attachments|import|send|folders> ...")
        subcommand = args[0]
        remainder = args[1:]
        mailbox, remainder = self._parse_flag_value(remainder, "--mailbox")

        if subcommand == "accounts":
            if remainder:
                raise TerminalCommandError("Usage: mail accounts")
            return await self._cmd_mail_accounts()

        entry, _selector_values = await self._resolve_terminal_mailbox(mailbox)
        tool_id = int(entry["tool_id"])

        if subcommand == "list":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            limit, remainder = self._parse_flag_value(remainder, "--limit")
            if remainder:
                raise TerminalCommandError("Usage: mail list [--mailbox <email>] [--folder INBOX] [--limit N]")
            return await email_builtin.list_emails(
                self.vfs.user,
                tool_id,
                folder=folder or "INBOX",
                limit=int(limit or 10),
            )

        if subcommand == "read":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            full = "--full" in remainder
            remainder = [item for item in remainder if item != "--full"]
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: mail read [--mailbox <email>] <id> [--folder F] [--full]"
                )
            return await email_builtin.read_email(
                self.vfs.user,
                tool_id,
                int(remainder[0]),
                folder=folder or "INBOX",
                preview_only=not full,
            )

        if subcommand == "attachments":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: mail attachments [--mailbox <email>] <id> [--folder F]"
                )
            return await email_builtin.list_email_attachments(
                self.vfs.user,
                tool_id,
                int(remainder[0]),
                folder=folder or "INBOX",
            )

        if subcommand == "folders":
            if remainder:
                raise TerminalCommandError("Usage: mail folders [--mailbox <email>]")
            return await email_builtin.list_mailboxes(self.vfs.user, tool_id)

        if subcommand == "import":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            attachment_id, remainder = self._parse_flag_value(remainder, "--attachment")
            output_path, remainder = self._parse_flag_value(remainder, "--output")
            if len(remainder) != 1 or not attachment_id:
                raise TerminalCommandError(
                    "Usage: mail import [--mailbox <email>] <id> --attachment <part> [--folder F] [--output PATH]"
                )
            message_id = int(remainder[0])
            _envelope, _message, _uid, attachments = await email_builtin._load_email_message_with_attachments(
                self.vfs.user,
                tool_id,
                message_id,
                folder=folder or "INBOX",
            )
            selected = next(
                (item for item in attachments if str(item.get("attachment_id")) == str(attachment_id)),
                None,
            )
            if selected is None:
                raise TerminalCommandError(f"Attachment {attachment_id} not found on email {message_id}.")
            source_name = str(selected.get("filename") or f"attachment-{attachment_id}")
            destination = output_path or posixpath.join(self.vfs.cwd, source_name)
            try:
                destination = await self.vfs.resolve_output_path(destination, source_name=source_name)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            written = await self._write_file_and_notify(
                destination,
                bytes(selected.get("content") or b""),
                mime_type=str(selected.get("mime_type") or "application/octet-stream"),
            )
            return self._format_write_result(f"Imported attachment to {written.path}", written)

        if subcommand == "send":
            to, remainder = self._parse_flag_value(remainder, "--to")
            cc, remainder = self._parse_flag_value(remainder, "--cc")
            subject, remainder = self._parse_flag_value(remainder, "--subject")
            body_file, remainder = self._parse_flag_value(remainder, "--body-file")
            attach_paths, remainder = self._parse_multi_flag(remainder, "--attach")
            if remainder or not to or not subject or not body_file:
                raise TerminalCommandError(
                    "Usage: mail send [--mailbox <email>] --to <addr> --subject <subject> "
                    "--body-file <path> [--cc <addr>] [--attach <path> ...]"
                )
            if not entry.get("can_send"):
                raise TerminalCommandError(
                    f"Sending is disabled for mailbox '{entry['selector_email']}'."
                )
            body = await self.vfs.read_text(body_file)
            return await self._send_mail_direct(
                tool_id=tool_id,
                to=to,
                cc=cc,
                subject=subject,
                body=body,
                attach_paths=attach_paths,
            )

        raise TerminalCommandError(f"Unknown mail subcommand: {subcommand}")

    async def _send_mail_direct(self, *, tool_id: int, to: str, cc: str | None,
                                subject: str, body: str, attach_paths: list[str]) -> str:
        credential = await email_builtin._get_credential(self.vfs.user, tool_id)
        if credential is None:
            raise TerminalCommandError("No email credential found.")

        smtp_server = credential.config.get("smtp_server")
        username = credential.config.get("username")
        from_address = credential.config.get("from_address", username)
        if not smtp_server:
            raise TerminalCommandError("SMTP server not configured.")

        msg = email_builtin.MIMEMultipart()
        msg["From"] = from_address
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(email_builtin.MIMEText(body, "plain"))

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
        email_builtin._attach_binary_parts(msg, attachments)

        server = None
        try:
            server = email_builtin.build_smtp_client(credential)
            recipients = [to]
            if cc:
                recipients.extend([item.strip() for item in cc.split(",") if str(item or "").strip()])
            server.sendmail(from_address, recipients, msg.as_string())
        finally:
            email_builtin.safe_smtp_quit(server)

        return f"Email sent successfully to {to}"

    @staticmethod
    def _extract_python_stdout(result: str) -> str:
        marker_stdout = "\nStdout: "
        marker_stderr = "\nStderr: "
        stdout_index = result.find(marker_stdout)
        if stdout_index == -1:
            return ""
        start = stdout_index + len(marker_stdout)
        stderr_index = result.find(marker_stderr, start)
        if stderr_index == -1:
            return result[start:]
        return result[start:stderr_index]

    async def _cmd_python(self, args: list[str]) -> str:
        if not self.capabilities.has_python:
            raise TerminalCommandError("Python execution is not enabled for this agent.")
        tool = self.capabilities.code_execution_tool
        config = await code_builtin.get_judge0_config(tool)
        host = config["url"]
        timeout = int(config.get("timeout") or 5)

        if not args:
            raise TerminalCommandError("Usage: python [--output PATH] <script.py> or python [--output PATH] -c \"...\"")

        output_path, remaining = self._parse_output_path(args)
        if remaining and remaining[0] == "-c":
            if len(remaining) != 2:
                raise TerminalCommandError("Usage: python [--output PATH] -c \"...\"")
            code = remaining[1]
            result = await code_builtin.execute_code(host, code, language="python", timeout=timeout)
        else:
            if len(remaining) != 1:
                raise TerminalCommandError("Usage: python [--output PATH] <script.py>")
            script_path = remaining[0]
            code = await self.vfs.read_text(script_path)
            result = await code_builtin.execute_code(host, code, language="python", timeout=timeout)

        if output_path:
            output_name = "python-stdout.txt"
            if remaining and remaining[0] != "-c":
                script_name = posixpath.basename(normalize_vfs_path(remaining[0], cwd=self.vfs.cwd)) or "python"
                stem, _ext = posixpath.splitext(script_name)
                output_name = f"{stem or 'python'}.stdout.txt"
            try:
                resolved_output = await self.vfs.resolve_output_path(
                    self._validate_text_write_path(output_path),
                    source_name=output_name,
                )
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            stdout = self._extract_python_stdout(result)
            written = await self._write_file_and_notify(
                resolved_output,
                stdout.encode("utf-8"),
                mime_type="text/plain",
                overwrite=True,
            )
            return self._append_warnings(result, written.warnings)

        return result
