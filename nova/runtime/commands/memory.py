from __future__ import annotations

from typing import TYPE_CHECKING

from nova.memory.service import search_memory_items
from nova.runtime.vfs import normalize_vfs_path

if TYPE_CHECKING:
    from nova.runtime.terminal import TerminalExecutor


def _terminal_command_error(*args, **kwargs):
    from nova.runtime.terminal import TerminalCommandError

    return TerminalCommandError(*args, **kwargs)


def format_memory_search_payload(payload: dict) -> str:
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


async def cmd_memory(executor: TerminalExecutor, args: list[str]) -> str:
    if not executor.capabilities.has_memory:
        raise _terminal_command_error("Memory commands are not enabled for this agent.")
    if not args or str(args[0] or "").strip().lower() != "search":
        raise _terminal_command_error(
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
                raise _terminal_command_error("Missing value after --limit")
            limit = executor._parse_int_flag("--limit", args[index])
        elif token == "--under":
            index += 1
            if index >= len(args):
                raise _terminal_command_error("Missing value after --under")
            under = normalize_vfs_path(args[index], cwd=executor.vfs.cwd)
        else:
            query_tokens.append(token)
        index += 1

    query = " ".join(query_tokens).strip()
    if not query:
        raise _terminal_command_error(
            "Usage: memory search <query> [--limit N] [--under /memory/path]"
        )

    payload = await search_memory_items(
        query=query,
        user=executor.vfs.user,
        limit=limit,
        under=under,
    )
    return format_memory_search_payload(payload)
