from __future__ import annotations

import json
import posixpath
from typing import TYPE_CHECKING

from nova.web.browser_service import BrowserSession, BrowserSessionError
from nova.web.download_service import download_http_file
from nova.web.search_service import SEARXNG_MAX_RESULTS, search_web

from nova.runtime.terminal_metrics import FAILURE_KIND_INVALID_ARGUMENTS

if TYPE_CHECKING:
    from nova.runtime.terminal import TerminalExecutor


BROWSER_SINGLE_PANE_ERROR = "Nova browser currently has a single active page; only `--pane 0` is available."
BROWSER_DEFAULT_ELEMENT_ATTRIBUTES = (
    "tagName",
    "href",
    "src",
    "data-src",
    "srcset",
    "alt",
    "title",
    "innerText",
)


def _terminal_command_error(*args, **kwargs):
    from nova.runtime.terminal import TerminalCommandError

    return TerminalCommandError(*args, **kwargs)


async def cmd_search(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    if not executor.capabilities.has_search:
        raise _terminal_command_error("Search commands are not enabled for this agent.")

    output_path, remaining = executor._parse_output_path(args)
    limit = None
    query_tokens: list[str] = []
    index = 0
    while index < len(remaining):
        token = remaining[index]
        if token == "--limit":
            index += 1
            if index >= len(remaining):
                raise _terminal_command_error("Missing value after --limit")
            limit = executor._parse_int_flag("--limit", remaining[index])
        else:
            query_tokens.append(token)
        index += 1

    query = " ".join(query_tokens).strip()
    if not query:
        raise _terminal_command_error("Usage: search <query> [--limit N] [--output /path.json]")

    try:
        effective_limit = None if limit is None else max(1, min(limit, SEARXNG_MAX_RESULTS))
        payload = await search_web(executor.capabilities.searxng_tool, query=query, limit=effective_limit)
    except Exception as exc:
        raise _terminal_command_error(str(exc)) from exc

    executor._last_search_results = list(payload.get("results") or [])

    if output_path:
        written = await executor._write_json_output(output_path, payload)
        return executor._format_write_result(f"Wrote search results to {written.path}", written)
    if capture_output:
        return executor._render_structured_stdout(payload, capture_output=True)

    lines: list[str] = []
    for index, result in enumerate(executor._last_search_results):
        title = str(result.get("title") or "").strip() or "(untitled)"
        url = str(result.get("url") or "").strip() or "(missing url)"
        snippet = str(result.get("snippet") or "").strip()
        line = f"{index}. {title} / {url}"
        if snippet:
            line += f" / {snippet}"
        lines.append(line)
    return "\n".join(lines) if lines else "No search results."


async def cmd_browse(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    if not executor.capabilities.has_web:
        raise _terminal_command_error("Browse commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error(
            "Usage: browse <open|ls|current|back|text|read|links|elements|click> ..."
        )
    action = args[0]
    remainder = args[1:]
    if action == "open":
        return await cmd_browse_open(executor, remainder)
    if action == "ls":
        return await cmd_browse_ls(executor, remainder)
    if action == "current":
        return await cmd_browse_current(executor, remainder)
    if action == "back":
        return await cmd_browse_back(executor, remainder)
    if action in {"text", "read"}:
        return await cmd_browse_text(executor, remainder, capture_output=capture_output)
    if action == "links":
        return await cmd_browse_links(executor, remainder, capture_output=capture_output)
    if action == "elements":
        return await cmd_browse_elements(executor, remainder, capture_output=capture_output)
    if action == "click":
        return await cmd_browse_click(executor, remainder)
    raise _terminal_command_error(
        "Usage: browse <open|ls|current|back|text|read|links|elements|click> ..."
    )


async def cmd_browse_open(executor: TerminalExecutor, args: list[str]) -> str:
    if not args:
        raise _terminal_command_error("Usage: browse open <url> | browse open --result N")

    result_index = None
    remaining: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--result":
            index += 1
            if index >= len(args):
                raise _terminal_command_error("Missing value after --result")
            result_index = executor._parse_int_flag("--result", args[index])
        else:
            remaining.append(token)
        index += 1

    session = await executor._get_browser_session()
    try:
        if result_index is not None:
            if remaining:
                raise _terminal_command_error("Usage: browse open <url> | browse open --result N")
            if not executor._last_search_results:
                raise _terminal_command_error(
                    "No cached search results are available in this run. Use `search` first."
                )
            opened = await session.open_search_result(result_index, executor._last_search_results)
        else:
            if len(remaining) != 1:
                raise _terminal_command_error("Usage: browse open <url> | browse open --result N")
            opened = await session.open(remaining[0])
    except BrowserSessionError as exc:
        raise _terminal_command_error(str(exc)) from exc

    status = opened.get("status")
    url = str(opened.get("url") or "")
    if status is None:
        return f"Opened {url}"
    return f"Opened {url} (status {status})"


async def cmd_browse_current(executor: TerminalExecutor, args: list[str]) -> str:
    _pane_index, remaining = executor._parse_browser_pane(args)
    if remaining:
        raise _terminal_command_error("Usage: browse current [--pane 0]")
    session = await executor._get_browser_session()
    try:
        return await session.current()
    except BrowserSessionError as exc:
        raise _terminal_command_error(str(exc)) from exc


async def cmd_browse_ls(executor: TerminalExecutor, args: list[str]) -> str:
    _pane_index, remaining = executor._parse_browser_pane(args)
    if remaining:
        raise _terminal_command_error("Usage: browse ls [--pane 0]")
    session = await executor._get_browser_session()
    try:
        current_url = await session.current()
    except BrowserSessionError as exc:
        raise _terminal_command_error(str(exc)) from exc
    return f"0  current  {current_url}"


async def cmd_browse_back(executor: TerminalExecutor, args: list[str]) -> str:
    _pane_index, remaining = executor._parse_browser_pane(args)
    if remaining:
        raise _terminal_command_error("Usage: browse back [--pane 0]")
    session = await executor._get_browser_session()
    try:
        payload = await session.back()
    except BrowserSessionError as exc:
        raise _terminal_command_error(str(exc)) from exc
    return f"Went back to {payload['url']} (status {payload['status']})"


async def cmd_browse_text(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    output_path, remaining = executor._parse_output_path(args)
    _pane_index, remaining = executor._parse_browser_pane(remaining)
    if len(remaining) > 1:
        raise _terminal_command_error("Usage: browse text [url] [--pane 0] [--output /path.txt]")
    inline_url = remaining[0] if remaining else None
    session = await executor._get_browser_session()
    await executor._browse_open_inline_url(session, inline_url)
    try:
        content = await session.extract_text()
    except BrowserSessionError as exc:
        raise _terminal_command_error(
            executor._format_browser_extraction_error(str(exc), inline_command="browse text")
        ) from exc
    if output_path:
        try:
            resolved_output = await executor.vfs.resolve_output_path(output_path)
            written = await executor._write_file_and_notify(
                resolved_output,
                content.encode("utf-8"),
                mime_type="text/plain",
            )
        except Exception as exc:
            raise _terminal_command_error(str(exc)) from exc
        return executor._format_write_result(f"Wrote page text to {written.path}", written)
    return content if capture_output else executor._truncate_output(content)


async def cmd_browse_links(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    output_path, remaining = executor._parse_output_path(args)
    _pane_index, remaining = executor._parse_browser_pane(remaining)
    absolute = False
    inline_url = None
    for token in remaining:
        if token == "--absolute":
            absolute = True
        elif inline_url is None:
            inline_url = token
        else:
            raise _terminal_command_error("Usage: browse links [url] [--pane 0] [--absolute] [--output /path.json]")
    session = await executor._get_browser_session()
    await executor._browse_open_inline_url(session, inline_url)
    try:
        links = await session.extract_links(absolute=absolute)
    except BrowserSessionError as exc:
        raise _terminal_command_error(
            executor._format_browser_extraction_error(str(exc), inline_command="browse links")
        ) from exc
    if output_path:
        written = await executor._write_json_output(output_path, links)
        return executor._format_write_result(f"Wrote page links to {written.path}", written)
    rendered = json.dumps(links, ensure_ascii=False, indent=2)
    return rendered if capture_output else executor._truncate_output(rendered)


async def cmd_browse_elements(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    output_path, remaining = executor._parse_output_path(args)
    _pane_index, remaining = executor._parse_browser_pane(remaining)
    attributes, remaining = executor._parse_multi_flag(remaining, "--attr")
    if len(remaining) not in {1, 2}:
        raise _terminal_command_error(
            "Usage: browse elements <selector> [url] [--pane 0] [--attr NAME ...] [--output /path.json]"
        )
    selector = remaining[0]
    inline_url = remaining[1] if len(remaining) == 2 else None
    attrs = attributes or list(BROWSER_DEFAULT_ELEMENT_ATTRIBUTES)
    session = await executor._get_browser_session()
    await executor._browse_open_inline_url(session, inline_url)
    try:
        elements = await session.get_elements(selector, attrs)
    except BrowserSessionError as exc:
        raise _terminal_command_error(
            executor._format_browser_extraction_error(str(exc), inline_command="browse elements")
        ) from exc
    payload = {
        "selector": selector,
        "attributes": attrs,
        "elements": elements,
    }
    if output_path:
        written = await executor._write_json_output(output_path, payload)
        return executor._format_write_result(f"Wrote selected elements to {written.path}", written)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    return rendered if capture_output else executor._truncate_output(rendered)


async def cmd_browse_click(executor: TerminalExecutor, args: list[str]) -> str:
    _pane_index, remaining = executor._parse_browser_pane(args)
    if len(remaining) != 1:
        raise _terminal_command_error("Usage: browse click <selector> [--pane 0]")
    session = await executor._get_browser_session()
    try:
        return await session.click(remaining[0])
    except BrowserSessionError as exc:
        raise _terminal_command_error(str(exc)) from exc


async def cmd_wget(executor: TerminalExecutor, args: list[str]) -> str:
    if not executor.capabilities.has_web:
        raise _terminal_command_error("Web commands are not enabled for this agent.")
    parsed = executor._parse_download_command(
        args,
        command_name="wget",
        usage="Usage: wget [-O <path>] [--output <path>] [-U <value>] [--user-agent <value>] [--header <name: value>] <url>",
        output_flags={"-O", "--output"},
        user_agent_flags={"-U", "--user-agent"},
        header_flags={"--header"},
    )
    content, mime_type, inferred_name = await executor._download_http(
        parsed.url,
        headers=parsed.headers,
        user_agent=parsed.user_agent,
    )
    destination = parsed.output_path or posixpath.join(executor.vfs.cwd, inferred_name)
    try:
        destination = await executor.vfs.resolve_output_path(destination, source_name=inferred_name)
    except Exception as exc:
        raise _terminal_command_error(str(exc)) from exc
    written = await executor._write_file_and_notify(destination, content, mime_type=mime_type)
    return executor._format_write_result(f"Downloaded {parsed.url} to {written.path}", written)


async def cmd_curl(executor: TerminalExecutor, args: list[str], *, capture_output: bool = False) -> str:
    if not executor.capabilities.has_web:
        raise _terminal_command_error("Web commands are not enabled for this agent.")
    parsed = executor._parse_download_command(
        args,
        command_name="curl",
        usage="Usage: curl [-o <path>] [--output <path>] [-A <value>] [--user-agent <value>] [-H <name: value>] [--header <name: value>] <url>",
        output_flags={"-o", "--output"},
        user_agent_flags={"-A", "--user-agent"},
        header_flags={"-H", "--header"},
    )
    content, mime_type, inferred_name = await executor._download_http(
        parsed.url,
        headers=parsed.headers,
        user_agent=parsed.user_agent,
    )
    if parsed.output_path:
        try:
            resolved_output = await executor.vfs.resolve_output_path(
                parsed.output_path,
                source_name=inferred_name,
            )
        except Exception as exc:
            raise _terminal_command_error(str(exc)) from exc
        written = await executor._write_file_and_notify(resolved_output, content, mime_type=mime_type)
        return executor._format_write_result(f"Downloaded {parsed.url} to {written.path}", written)
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
        try:
            decoded = content.decode("utf-8")
            return decoded if capture_output else decoded[:8000]
        except UnicodeDecodeError:
            if capture_output:
                raise _terminal_command_error(
                    "curl only supports text output when used in a pipeline or shell redirection. "
                    "Use curl --output <path> to save binary responses.",
                    failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
                )
    if capture_output:
        raise _terminal_command_error(
            "curl only supports text output when used in a pipeline or shell redirection. "
            "Use curl --output <path> to save binary responses.",
            failure_kind=FAILURE_KIND_INVALID_ARGUMENTS,
        )
    return (
        f"Binary response from {parsed.url} ({mime_type}, {len(content)} bytes). "
        f"Use curl --output {posixpath.join(executor.vfs.cwd, inferred_name)} to save it."
    )
