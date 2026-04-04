from __future__ import annotations

from .capabilities import TerminalCapabilities


def build_skill_registry(capabilities: TerminalCapabilities) -> dict[str, str]:
    skills = {
        "terminal.md": """# Terminal

The main action surface is the persistent Nova terminal.

Start with:
- `pwd`
- `ls`
- `ls /thread`
- `ls /skills`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
""",
    }

    if capabilities.has_email:
        skills["mail.md"] = """# Mail

Mail is accessed through shell-like commands:

- `mail list`
- `mail read <id>`
- `mail attachments <id>`
- `mail import <id> --attachment <part> --output /workspace/<name>`
- `mail send --to ... --subject ... --body-file /workspace/body.txt --attach /workspace/file.pdf`

Prefer reading attachments metadata first, then importing only the files you need.
Imported attachments become normal files in the terminal workspace.
"""

    if capabilities.has_web:
        skills["web.md"] = """# Web

Web downloads are exposed through familiar commands:

- `wget <url>`
- `wget <url> --output /workspace/file.ext`
- `curl <url>`
- `curl <url> --output /workspace/file.ext`

Use `curl` without `--output` only when you want a text preview.
Use `wget` or `curl --output` when you need a reusable file.
"""

    if capabilities.has_python:
        skills["python.md"] = """# Python

Python execution is available through:

- `python /workspace/script.py`
- `python -c "print('hello')"`

Keep scripts in `/workspace` when they are reused across multiple commands.
"""

    if capabilities.has_subagents:
        skills["subagents.md"] = """# Sub-agents

Sub-agents are delegated through the dedicated `delegate_to_agent` tool, not
through terminal commands.

Use sub-agents when a specialized configured agent can handle a focused task.
Pass terminal file paths in `input_paths` when the child agent needs local files.
"""

    return skills
