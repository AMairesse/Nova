from __future__ import annotations

import posixpath
import re
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

from nova.memory.service import MEMORY_ROOT
from nova.runtime.terminal_metrics import FAILURE_KIND_INVALID_ARGUMENTS
from nova.runtime.vfs import HISTORY_ROOT, INBOX_ROOT, VFSError, normalize_vfs_path

if TYPE_CHECKING:
    from nova.runtime.terminal import TerminalExecutor


def _terminal_command_error(*args, **kwargs):
    from nova.runtime.terminal import TerminalCommandError

    return TerminalCommandError(*args, **kwargs)


async def cmd_ls(executor: TerminalExecutor, args: list[str]) -> str:
    options, raw_paths = executor._parse_ls_flags(args)
    requested_paths = raw_paths or [executor.vfs.cwd]
    targets: list[str] = []
    for raw_path in requested_paths:
        expanded = await executor._expand_ls_target(raw_path)
        for normalized in expanded:
            if normalized not in targets:
                targets.append(normalized)

    if len(targets) == 1:
        normalized = targets[0]
        if not await executor.vfs.is_dir(normalized):
            entry = await executor._lookup_ls_entry(normalized)
            return executor._format_ls_entry(
                entry,
                long_format=options["long_format"],
                human_readable=options["human_readable"],
            )
        if options["recursive"]:
            sections = await executor._render_ls_recursive_sections(
                normalized,
                show_all=options["show_all"],
                long_format=options["long_format"],
                human_readable=options["human_readable"],
            )
            return "\n\n".join(section for section in sections if section)
        lines = await executor._render_ls_directory(
            normalized,
            show_all=options["show_all"],
            long_format=options["long_format"],
            human_readable=options["human_readable"],
        )
        return "\n".join(lines)

    rendered_sections: list[str] = []
    for normalized in targets:
        if await executor.vfs.is_dir(normalized):
            if options["recursive"]:
                sections = await executor._render_ls_recursive_sections(
                    normalized,
                    show_all=options["show_all"],
                    long_format=options["long_format"],
                    human_readable=options["human_readable"],
                )
                section = "\n\n".join(item for item in sections if item)
            else:
                lines = await executor._render_ls_directory(
                    normalized,
                    show_all=options["show_all"],
                    long_format=options["long_format"],
                    human_readable=options["human_readable"],
                )
                section = normalized if not lines else f"{normalized}:\n" + "\n".join(lines)
        else:
            entry = await executor._lookup_ls_entry(normalized)
            section = executor._format_ls_entry(
                entry,
                long_format=options["long_format"],
                human_readable=options["human_readable"],
            )
        rendered_sections.append(section)
    return "\n\n".join(section for section in rendered_sections if section)


async def cmd_cd(executor: TerminalExecutor, args: list[str]) -> str:
    target = args[0] if args else "/"
    normalized = normalize_vfs_path(target, cwd=executor.vfs.cwd)
    if not await executor.vfs.path_exists(normalized) or not await executor.vfs.is_dir(normalized):
        raise _terminal_command_error(f"Directory not found: {normalized}")
    executor.vfs.set_cwd(normalized)
    return executor.vfs.cwd


async def cmd_cat(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None) -> str:
    usage = "cat [-n] [<path>]"
    flags, positionals, _numeric_count = executor._parse_short_flags(
        args,
        command_name=usage,
        supported_flags={"n"},
    )
    if len(positionals) > 1:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    if not positionals:
        if stdin_text is None:
            raise _terminal_command_error(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        content = str(stdin_text)
        return executor._number_lines(content) if "n" in flags else content
    try:
        content = await executor.vfs.read_text(positionals[0])
    except VFSError as exc:
        raise _terminal_command_error(str(exc)) from exc
    return executor._number_lines(content) if "n" in flags else content


async def cmd_head_tail(
    executor: TerminalExecutor,
    args: list[str],
    *,
    tail: bool,
    stdin_text: str | None = None,
) -> str:
    command = "tail" if tail else "head"
    usage = f"{command} [-n N|-N|-c N] [<path>]"
    flags, positionals, numeric_count = executor._parse_short_flags(
        args,
        command_name=usage,
        supported_flags={"n", "c"},
        allow_numeric_count=True,
    )
    if "n" in flags and "c" in flags:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    line_count = 10
    byte_count: int | None = None
    if "n" in flags:
        if not positionals:
            raise _terminal_command_error(
                "Missing value after -n",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        line_count = max(0, executor._parse_int_flag("-n", positionals[0]))
        positionals = positionals[1:]
    elif "c" in flags:
        if not positionals:
            raise _terminal_command_error(
                "Missing value after -c",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        byte_count = max(0, executor._parse_int_flag("-c", positionals[0]))
        positionals = positionals[1:]
    if numeric_count is not None:
        if byte_count is not None:
            raise _terminal_command_error(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        line_count = max(0, numeric_count)
    if len(positionals) > 1:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    if not positionals:
        if stdin_text is None:
            raise _terminal_command_error(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        content = str(stdin_text)
    else:
        content = await cmd_cat(executor, [positionals[0]])
    if byte_count is not None:
        payload = content.encode("utf-8")
        selected_bytes = payload[-byte_count:] if tail else payload[:byte_count]
        return selected_bytes.decode("utf-8", errors="ignore")
    lines = content.splitlines()
    selected = lines[-line_count:] if tail else lines[:line_count]
    return "\n".join(selected)


async def cmd_mkdir(executor: TerminalExecutor, args: list[str]) -> str:
    flags, positionals, _numeric_count = executor._parse_short_flags(
        args,
        command_name="mkdir [-p] <path> [<path> ...]",
        supported_flags={"p"},
    )
    if not positionals:
        raise _terminal_command_error("Usage: mkdir [-p] <path> [<path> ...]")

    recursive = "p" in flags
    results: list[str] = []
    for raw_path in positionals:
        try:
            if recursive:
                created = await executor._mkdir_recursive_and_notify(raw_path)
                results.append(f"Ensured directory {created}")
            else:
                created = await executor._mkdir_and_notify(raw_path)
                results.append(f"Created directory {created}")
        except VFSError as exc:
            raise _terminal_command_error(str(exc)) from exc
    return "\n".join(results)


async def cmd_rmdir(executor: TerminalExecutor, args: list[str]) -> str:
    if not args:
        raise _terminal_command_error(
            "Usage: rmdir <path> [<path> ...]",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    removed_messages: list[str] = []
    for raw_path in args:
        normalized = normalize_vfs_path(raw_path, cwd=executor.vfs.cwd)
        if not await executor.vfs.path_exists(normalized):
            raise _terminal_command_error(f"Path not found: {normalized}")
        if not await executor.vfs.is_dir(normalized):
            raise _terminal_command_error(f"Not a directory: {normalized}")
        try:
            removed = await executor._remove_and_notify(raw_path, recursive=False)
        except VFSError as exc:
            raise _terminal_command_error(str(exc)) from exc
        removed_messages.append(f"Removed {removed}")
    return "\n".join(removed_messages)


async def cmd_printf(executor: TerminalExecutor, args: list[str]) -> str:
    if not args:
        raise _terminal_command_error(
            "Usage: printf <format> [arg ...]",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    format_text = executor._decode_escaped_text(args[0])
    values = [str(item) for item in args[1:]]
    parts = executor._parse_printf_parts(format_text)
    specifiers = [value for kind, value in parts if kind == "spec"]
    if not specifiers:
        return format_text

    chunk_size = len(specifiers)
    iterations = max(1, (len(values) + chunk_size - 1) // chunk_size)
    output_parts: list[str] = []
    for iteration_index in range(iterations):
        chunk = values[iteration_index * chunk_size:(iteration_index + 1) * chunk_size]
        spec_index = 0
        for kind, value in parts:
            if kind == "literal":
                output_parts.append(value)
                continue
            arg_value = chunk[spec_index] if spec_index < len(chunk) else None
            output_parts.append(executor._format_printf_value(value, arg_value))
            spec_index += 1
    return "".join(output_parts)


async def cmd_touch(executor: TerminalExecutor, args: list[str]) -> str:
    if len(args) != 1:
        raise _terminal_command_error("Usage: touch <path>")
    normalized = executor._validate_text_write_path(args[0])
    if await executor.vfs.is_dir(normalized):
        raise _terminal_command_error(f"Cannot touch a directory: {normalized}")
    if await executor.vfs.path_exists(normalized):
        return f"Touched {normalized}"
    try:
        written = await executor._write_file_and_notify(normalized, b"", mime_type="text/plain")
        return executor._format_write_result(f"Created empty file {written.path}", written)
    except VFSError as exc:
        raise _terminal_command_error(str(exc)) from exc


async def cmd_tee(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None) -> str:
    if not args:
        raise _terminal_command_error('Usage: tee <path> [--text "<content>"] [--append]')
    append = "--append" in args
    remainder = [item for item in args if item != "--append"]
    text, remainder = executor._parse_flag_value(remainder, "--text")
    if len(remainder) != 1:
        raise _terminal_command_error('Usage: tee <path> [--text "<content>"] [--append]')
    if text is not None and stdin_text is not None:
        raise _terminal_command_error(
            "tee cannot combine --text with piped or redirected input.",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    if text is None and stdin_text is None:
        raise _terminal_command_error('Usage: tee <path> [--text "<content>"] [--append]')

    normalized = executor._validate_text_write_path(remainder[0])
    if await executor.vfs.is_dir(normalized):
        raise _terminal_command_error(f"Cannot write text into a directory: {normalized}")

    if text is not None:
        content = executor._decode_escaped_text(str(text))
        written = await executor._write_shell_output(normalized, content, append=append)
        return executor._format_write_result(
            f"Wrote {len(content.encode('utf-8'))} bytes to {written.path}",
            written,
        )

    content = str(stdin_text or "")
    await executor._write_shell_output(normalized, content, append=append)
    return content


async def cmd_cp(executor: TerminalExecutor, args: list[str]) -> str:
    if len(args) != 2:
        raise _terminal_command_error("Usage: cp <source> <destination>")
    try:
        copied = await executor._copy_and_notify(args[0], args[1])
        return f"Copied to {copied.path}"
    except VFSError as exc:
        raise _terminal_command_error(str(exc)) from exc


async def cmd_mv(executor: TerminalExecutor, args: list[str]) -> str:
    if len(args) != 2:
        raise _terminal_command_error("Usage: mv <source> <destination>")
    try:
        destination = await executor._move_and_notify(args[0], args[1])
        return f"Moved to {destination}"
    except VFSError as exc:
        raise _terminal_command_error(str(exc)) from exc


async def cmd_rm(executor: TerminalExecutor, args: list[str]) -> str:
    usage = "rm [-f] [-r|-R] <path> [<path> ...]"
    flags, positionals, _numeric_count = executor._parse_short_flags(
        args,
        command_name=usage,
        supported_flags={"f", "r", "R"},
    )
    if not positionals:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    removed_messages: list[str] = []
    force = "f" in flags
    recursive = "r" in flags or "R" in flags
    for path in positionals:
        try:
            removed = await executor._remove_and_notify(path, recursive=recursive)
        except VFSError as exc:
            message = str(exc)
            if force and message.startswith("Path not found:"):
                continue
            raise _terminal_command_error(message) from exc
        removed_messages.append(f"Removed {removed}")
    return "\n".join(removed_messages)


async def cmd_find(executor: TerminalExecutor, args: list[str]) -> str:
    usage = "find <path> [<path> ...] [-type f|d] [-name <glob> [-o -name <glob> ...]]"
    if not args:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    roots: list[str] = []
    index = 0
    while index < len(args):
        token = str(args[index] or "").strip()
        if not token:
            index += 1
            continue
        if token.startswith("-") or token in {"(", ")", "!"}:
            break
        roots.append(token)
        index += 1

    if not roots:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    desired_type: str | None = None
    name_patterns: list[str] = []
    remaining = list(args[index:])
    expect_name_after_or = False
    clause_active = False
    cursor = 0

    def _unsupported_find_expression():
        return _terminal_command_error(
            f"Unsupported find expression. Supported form: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    while cursor < len(remaining):
        token = str(remaining[cursor] or "").strip()
        if token == "-o":
            if not clause_active:
                raise _unsupported_find_expression()
            expect_name_after_or = True
            clause_active = False
            cursor += 1
            continue
        if token == "-type":
            if cursor + 1 >= len(remaining):
                raise _terminal_command_error(
                    f"Usage: {usage}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            type_value = str(remaining[cursor + 1] or "").strip()
            if type_value not in {"f", "d"}:
                raise _terminal_command_error(
                    f"Unsupported find type: {type_value}",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            if desired_type is not None:
                raise _unsupported_find_expression()
            desired_type = type_value
            cursor += 2
            continue
        if token == "-name":
            if cursor + 1 >= len(remaining):
                raise _terminal_command_error(
                    "Missing value for -name",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
            if name_patterns and not expect_name_after_or:
                raise _unsupported_find_expression()
            name_patterns.append(str(remaining[cursor + 1] or ""))
            clause_active = True
            expect_name_after_or = False
            cursor += 2
            continue
        raise _unsupported_find_expression()

    if expect_name_after_or:
        raise _unsupported_find_expression()

    collected: set[str] = set()
    for raw_root in roots:
        normalized_root = normalize_vfs_path(raw_root, cwd=executor.vfs.cwd)
        if not await executor.vfs.path_exists(normalized_root):
            raise _terminal_command_error(f"Path not found: {normalized_root}")
        try:
            collected.update(await executor.vfs.find(normalized_root, ""))
        except VFSError as exc:
            raise _terminal_command_error(str(exc)) from exc

    filtered: list[str] = []
    for path in sorted(collected):
        if name_patterns:
            basename = posixpath.basename(path.rstrip("/")) or path
            if not any(fnmatch(basename, pattern) for pattern in name_patterns):
                continue
        if desired_type is not None:
            is_dir = await executor.vfs.is_dir(path)
            if desired_type == "f" and is_dir:
                continue
            if desired_type == "d" and not is_dir:
                continue
        filtered.append(path)
    return "\n".join(filtered)


def _sort_text_lines(content: str) -> str:
    text = str(content or "")
    lines = text.splitlines()
    if not lines:
        return ""
    rendered = "\n".join(sorted(lines))
    if text.endswith("\n"):
        rendered += "\n"
    return rendered


async def cmd_sort(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None) -> str:
    usage = "sort [<path>]"
    positionals: list[str] = []
    for token in args:
        if token.startswith("-") and token != "-":
            raise _terminal_command_error(
                f"Unsupported sort flag: {token}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        positionals.append(token)

    if len(positionals) > 1:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    if not positionals:
        if stdin_text is None:
            raise _terminal_command_error(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        return _sort_text_lines(stdin_text)

    try:
        content = await executor.vfs.read_text(positionals[0])
    except VFSError as exc:
        raise _terminal_command_error(str(exc)) from exc
    return _sort_text_lines(content)


async def cmd_file(executor: TerminalExecutor, args: list[str]) -> str:
    if not args:
        raise _terminal_command_error(
            "Usage: file <path> [<path> ...]",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    lines: list[str] = []
    for raw_path in args:
        entry = await executor._resolve_vfs_entry(raw_path)
        normalized = normalize_vfs_path(raw_path, cwd=executor.vfs.cwd)
        if entry.get("type") == "dir":
            lines.append(f"{normalized}: directory")
            continue
        mime_type = str(entry.get("mime_type") or "").strip()
        size = int(entry.get("size") if entry.get("size") is not None else 0)
        if mime_type:
            lines.append(f"{normalized}: {mime_type}, {size} bytes")
        else:
            lines.append(f"{normalized}: file, {size} bytes")
    return "\n".join(lines)


async def cmd_grep(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None) -> str:
    if not args:
        raise _terminal_command_error("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")

    flags, remaining, _numeric_count = executor._parse_short_flags(
        args,
        command_name="grep [-r] [-i] [-n] <pattern> [<path>]",
        supported_flags={"r", "i", "n"},
    )
    recursive = "r" in flags
    ignore_case = "i" in flags
    show_numbers = "n" in flags

    if len(remaining) not in {1, 2}:
        raise _terminal_command_error("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")

    pattern = remaining[0]
    candidates: list[str] = []
    stdin_candidate = len(remaining) == 1
    if stdin_candidate:
        if recursive:
            raise _terminal_command_error(
                "grep -r requires a path.",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        if stdin_text is None:
            raise _terminal_command_error("Usage: grep [-r] [-i] [-n] <pattern> [<path>]")
    else:
        raw_path = remaining[1]
        normalized_path = normalize_vfs_path(raw_path, cwd=executor.vfs.cwd)
        if not await executor.vfs.path_exists(normalized_path):
            raise _terminal_command_error(f"Path not found: {normalized_path}")

        if await executor.vfs.is_dir(normalized_path):
            if not recursive:
                raise _terminal_command_error("grep on directories requires -r")
            try:
                candidates = await executor.vfs.find(normalized_path, "")
            except VFSError as exc:
                raise _terminal_command_error(str(exc)) from exc
        else:
            candidates = [normalized_path]

    results: list[str] = []
    regex_flags = re.IGNORECASE if ignore_case else 0
    try:
        matcher = re.compile(pattern, regex_flags)
    except re.error as exc:
        raise _terminal_command_error(f"Invalid grep pattern: {exc}") from exc

    if stdin_candidate:
        for line_number, line in enumerate(str(stdin_text or "").splitlines(), start=1):
            if matcher.search(line):
                prefix = f"stdin:{line_number}:" if show_numbers else ""
                results.append(f"{prefix}{line}")
        return "\n".join(results)

    for candidate in candidates:
        if await executor.vfs.is_dir(candidate):
            continue
        try:
            content = await executor.vfs.read_text(candidate)
        except VFSError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if matcher.search(line):
                if show_numbers:
                    results.append(f"{candidate}:{line_number}:{line}")
                else:
                    results.append(f"{candidate}:{line}")
    return "\n".join(results)


async def cmd_wc(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None) -> str:
    usage = "wc [-l] [-w] [-c] [<path>]"
    flags, positionals, _numeric_count = executor._parse_short_flags(
        args,
        command_name=usage,
        supported_flags={"l", "w", "c"},
    )
    if len(positionals) > 1:
        raise _terminal_command_error(
            f"Usage: {usage}",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )

    if not positionals:
        if stdin_text is None:
            raise _terminal_command_error(
                f"Usage: {usage}",
                failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
            )
        content = str(stdin_text)
        path_label = ""
    else:
        path_label = normalize_vfs_path(positionals[0], cwd=executor.vfs.cwd)
        content = await cmd_cat(executor, [positionals[0]])

    counts = {
        "l": executor._count_text_lines(content),
        "w": len(str(content or "").split()),
        "c": len(str(content or "").encode("utf-8")),
    }
    selected_flags = [flag for flag in ("l", "w", "c") if flag in flags]
    if not selected_flags:
        selected_flags = ["l", "w", "c"]
    values = [str(counts[flag]) for flag in selected_flags]
    if path_label:
        values.append(path_label)
    return " ".join(values)
