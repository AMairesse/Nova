from __future__ import annotations

from typing import Any

from nova.models.Thread import Thread


def build_runtime_system_prompt(
    *,
    capabilities,
    thread_mode: str | None = None,
    tools_enabled: bool = True,
    allow_ask_user: bool = True,
    source_message_id: int | None = None,
    agent_instructions: str = "",
) -> str:
    prompt = build_automatic_runtime_instructions(
        capabilities=capabilities,
        thread_mode=thread_mode,
        tools_enabled=tools_enabled,
        allow_ask_user=allow_ask_user,
        source_message_id=source_message_id,
    )
    agent_instructions = str(agent_instructions or "").strip()
    if agent_instructions:
        prompt += f"\n\nAgent instructions:\n{agent_instructions}\n"
    return prompt


def build_automatic_runtime_instructions(
    *,
    capabilities,
    thread_mode: str | None = None,
    tools_enabled: bool = True,
    allow_ask_user: bool = True,
    source_message_id: int | None = None,
) -> str:
    if not tools_enabled:
        return (
            "Runtime instructions:\n"
            "- Tool use is unavailable for the selected provider/model in this run.\n"
            "- Do not call terminal, delegate_to_agent, or ask_user.\n"
            "- Use only the available conversation context.\n"
        )

    families = list(capabilities.enabled_command_families())
    if thread_mode == Thread.Mode.CONTINUOUS and "history" not in families:
        families.append("history")

    filesystem_lines = [
        "- /: persistent files for this thread, including files added from the Files panel",
        "- /inbox: files attached to the current user message, when present",
        "- /history: files attached to earlier live messages in this conversation",
        "- /skills: readonly recipes",
        "- /tmp: scratch files hidden from the normal file sidebar",
        "- /subagents/<subagent-slug>-<run-id>/: files returned by delegated sub-agents",
    ]
    if capabilities.has_memory:
        filesystem_lines.insert(2, "- /memory: shared user-scoped long-term memory")
    if capabilities.has_webdav:
        filesystem_lines.insert(2, "- /webdav: remote WebDAV mounts configured for this agent")

    lines: list[str] = [
        "Runtime instructions:",
        "- The main action surface is the `terminal` tool.",
        "- Use shell-like commands for terminal work.",
    ]
    if allow_ask_user:
        lines.append("- Use `ask_user` only for genuine blocking clarifications.")
    lines.extend(
        [
            "- The terminal session is persistent for this agent and thread.",
            "",
            "Filesystem layout:",
            *filesystem_lines,
            "",
            "Operational rules:",
            "- Inspect `/skills` with `ls /skills` and `cat /skills/<file>.md` for detailed capability guidance.",
            "- If the current working directory matters and is unknown, run `pwd` first.",
            f"- Enabled command families: {', '.join(families)}.",
            f"- Configured sub-agents: {_format_subagents(capabilities.subagents)}.",
            "- Use `delegate_to_agent` only for configured sub-agents. Pass the sub-agent id, exact name, or composite selector.",
            "- Keep thread-scoped file organization, cleanup, and webapp lifecycle work in the main terminal session.",
            "- Use sub-agents only for focused specialist work; integrate returned outputs before finalizing.",
            "- Files uploaded in the Files panel are persistent thread files under `/`.",
            "- If the user refers to a file without a path, inspect `/` first with `ls /` or `find / -name ...`.",
            "- Use `/inbox` only for files attached to the current user message and `/history` only for earlier live-message attachments.",
        ]
    )
    if source_message_id is not None:
        lines.append(
            "- Current-message attachments are under `/inbox` when present; older live-message attachments are under `/history`. "
            "Only fall back to those mounts when the request clearly points to current or earlier chat attachments."
        )
        lines.append(
            "- Only claim to have used a reference file when it was read directly or passed explicitly to a sub-agent."
        )
    lines.append(
        "- Final responses may link existing thread files with `[label](/path/file.ext)` or display images with `![alt](/path/image.png)`."
    )

    if thread_mode == Thread.Mode.CONTINUOUS:
        lines.append(
            "- Continuous threads may include prior-day summaries and a recent raw-message window; use `history search` then `history get` for older evidence."
        )
    if capabilities.has_date_time:
        lines.append("- Use `date` for current date/time queries.")
    if capabilities.has_memory:
        lines.append(
            "- Use `/memory` for user-scoped durable memory; use `grep` for lexical matching and `memory search` for hybrid retrieval."
        )
    if capabilities.has_python:
        lines.append(
            "- Use `python` inside the persistent terminal for computation, data processing, scripts, and package-backed workflows; if an import is missing, run `pip install --user <package>` and retry."
        )
        lines.append(
            "- Use `python --workdir /project -c \"...\"` when inline code needs to sync a workspace folder; keep cleanup, moves, file organization, and `webapp expose` in terminal commands."
        )
    if capabilities.has_calendar:
        calendar_line = "- Use `calendar` commands for CalDAV accounts and events; run `calendar accounts` first when account selection is unclear."
        if capabilities.has_multiple_calendar_accounts:
            calendar_line += " When several accounts exist, pass `--account <selector>` explicitly."
        calendar_line += " Recurring events are readable, but create/update/delete only support non-recurring events."
        lines.append(calendar_line)
    if capabilities.has_search:
        search_line = "- Use `search` for web discovery."
        if capabilities.has_web:
            search_line += " Search results are cached for the current run and can be opened with `browse open --result N` using 0-based indexes."
        lines.append(search_line)
    if capabilities.has_web:
        lines.append(
            "- Use `browse` for interactive page reading within the current run only; persist needed outputs with `--output`, or use `curl`/`wget` for direct downloads."
        )
    if capabilities.has_webdav:
        lines.append(
            "- Use `/webdav` as a remote filesystem mount; normal file commands apply, subject to configured WebDAV permissions."
        )
    if capabilities.has_webapp:
        lines.append(
            "- Build static webapps in the persistent filesystem, then publish with `webapp expose <source_dir>`; published apps update as source files change."
        )
        lines.append(
            "- For HTML/CSS/JS files, use raw characters rather than escaped markup and prefer `tee ... --text` for long content."
        )
    if capabilities.has_mcp:
        lines.append(
            "- Use `mcp tools` and `mcp schema` before remote MCP calls; persist machine-readable results with `--output`, `--extract-to`, or shell redirection."
        )
    if capabilities.has_api:
        lines.append(
            "- Use `api operations` and `api schema` before custom API calls; persist structured results with `--output` or shell redirection."
        )
    if capabilities.has_multiple_mailboxes:
        lines.append("- When using mail commands, always pass `--mailbox <email>` explicitly.")
    if allow_ask_user:
        lines.append("- Ask one combined clarification question at a time.")

    return "\n".join(lines).rstrip() + "\n"


def _format_subagents(subagents: list[Any]) -> str:
    return ", ".join(_format_subagent_prompt_entry(subagent) for subagent in subagents) or "none"


def _format_subagent_prompt_entry(subagent) -> str:
    label = f"{subagent.id}:{subagent.name}"
    details: list[str] = []
    description = str(getattr(subagent, "tool_description", "") or "").strip().rstrip(".")
    if description:
        details.append(description)
    response_mode = str(getattr(subagent, "default_response_mode", "") or "").strip().lower()
    if response_mode:
        details.append(f"{response_mode} output")
    if not details:
        return label
    return f"{label} [{'; '.join(details)}]"
