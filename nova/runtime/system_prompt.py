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
        "- Use shell-like commands for terminal work. This is a documented subset, not bash: "
        "redirections (`>`, `>>`, `2>`, `2>&1`, `&>`, `<`), pipes, `;`, `&&`, and `||` are supported. "
        "Loops, functions, heredocs, and substitutions are rejected."
    ]
    lines.extend(
        [
            "",
            "Filesystem layout:",
            *filesystem_lines,
            "",
            "Operational rules:",
            "- Inspect `/skills` with `ls /skills` and `cat /skills/<file>.md` for detailed capability guidance.",
            "- If the current working directory matters and is unknown, run `pwd` first.",
            f"- Enabled command families: {', '.join(families)}.",
            f"- Configured sub-agents: {_format_subagents(capabilities.subagents)}.",
            "- Keep thread-scoped file organization, cleanup, and webapp lifecycle work in the main terminal session.",
            "- Integrate returned sub-agent outputs before finalizing.",
            "- If the user refers to a file without a path, inspect `/` first with `ls /` or `find / -name ...`.",
        ]
    )
    if source_message_id is not None:
        lines.append(
            "- Use attachment mounts only when the request clearly points to current or earlier chat attachments."
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
    if capabilities.has_python:
        lines.append("- If a Python import is missing, run `pip install --user <package>` and retry.")
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
