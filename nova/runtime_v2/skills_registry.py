from __future__ import annotations

from .capabilities import TerminalCapabilities


def build_skill_registry(capabilities: TerminalCapabilities) -> dict[str, str]:
    skills = {
        "terminal.md": """# Terminal

The main action surface is the persistent Nova terminal.

Start with:
- `pwd`
- `ls`
- `ls /`
- `ls /skills`
- `touch /note.txt`
- `tee /note.txt --text "hello"`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
""",
    }

    if capabilities.has_email:
        mailbox_note = (
            "\nWhen several mailboxes are configured, always pass `--mailbox <email>`.\n"
            if capabilities.has_multiple_mailboxes
            else "\nIf only one mailbox is configured, `--mailbox` is optional.\n"
        )
        skills["mail.md"] = f"""# Mail

Mail is accessed through shell-like commands:

- `mail accounts`
- `mail list`
- `mail read <id>`
- `mail attachments <id>`
- `mail import <id> --attachment <part> --output /attachment.bin`
- `mail folders --mailbox <email>`
- `mail send --mailbox <email> --to ... --subject ... --body-file /body.txt --attach /file.pdf`

Prefer reading attachments metadata first, then importing only the files you need.
Imported attachments become normal files in the terminal filesystem.
{mailbox_note}Reuse the same mailbox throughout a workflow unless the user explicitly asks you to switch.
"""

    if capabilities.has_web:
        skills["web.md"] = """# Web

Web downloads are exposed through familiar commands:

- `wget <url>`
- `wget <url> --output /downloads/file.ext`
- `curl <url>`
- `curl <url> --output /downloads/file.ext`

Use `curl` without `--output` only when you want a text preview.
Use `wget` or `curl --output` when you need a reusable file.
"""

    if capabilities.has_python:
        skills["python.md"] = """# Python

Python execution is available through:

- `python /script.py`
- `python -c "print('hello')"`
- `python --output /result.txt /script.py`
- `python --output /result.txt -c "print('hello')"`

Keep scripts at stable paths in `/` when they are reused across multiple commands.
Typical workflow:
- create a script with `tee /script.py --text "..."`
- run it with `python /script.py`
- capture stdout into a file with `python --output /result.txt /script.py`
"""

    if capabilities.has_date_time:
        skills["date.md"] = """# Date / Time

Use the native `date` command for current date and time:

- `date`
- `date -u`
- `date +%F`
- `date +%T`

For more advanced date arithmetic, use Python instead of expecting full GNU date support.
"""

    if capabilities.has_subagents:
        skills["subagents.md"] = """# Sub-agents

Sub-agents are delegated through the dedicated `delegate_to_agent` tool, not
through terminal commands.

Use sub-agents when a specialized configured agent can handle a focused task.
Pass terminal file paths in `input_paths` when the child agent needs local files.
The child agent receives copied inputs under `/inbox`.
Files created or modified by the child are copied back automatically under
`/subagents/<agent-id>-<run-id>/`.
"""

    return skills
