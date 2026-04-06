from __future__ import annotations

from nova.models.Thread import Thread

from .capabilities import TerminalCapabilities


def build_skill_registry(
    capabilities: TerminalCapabilities,
    *,
    thread_mode: str | None = None,
) -> dict[str, str]:
    skills = {
        "terminal.md": """# Terminal

The main action surface is the persistent Nova terminal.

Start with:
- `pwd`
- `ls -la`
- `ls /skills`
- `echo "hello" > /note.txt`
- `cat /note.txt | grep hello`
- `tee /note.txt --text "first line\\nsecond line"`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
Minimal text pipes plus `<`, `>`, and `>>` are supported, but this is not a full shell.
""",
    }

    if thread_mode == Thread.Mode.CONTINUOUS:
        skills["continuous.md"] = """# Continuous Mode

This thread runs in continuous mode.

The model already receives:
- summaries from previous days when available
- a recent raw message window for today

When you need older evidence or exact passages, use:
- `history search <query>`
- `history get --message <id>`
- `history get --day-segment <id>`

Use `history search` first to locate the right day segment or message, then
`history get` to retrieve exact content before answering.
"""

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

    if capabilities.has_calendar:
        account_note = (
            "\nWhen several calendar accounts are configured, always pass `--account <selector>`.\n"
            if capabilities.has_multiple_calendar_accounts
            else "\nIf only one calendar account is configured, `--account` is optional.\n"
        )
        skills["calendar.md"] = f"""# Calendar

CalDAV calendars are accessed through `calendar` commands:

- `calendar accounts`
- `calendar calendars`
- `calendar upcoming --days 7`
- `calendar list --from 2026-04-01 --to 2026-04-07`
- `calendar search roadmap --days 30`
- `calendar show <event-id>`
- `calendar create --calendar Work --title "Planning" --start 2026-04-06T09:00:00+02:00`
- `calendar update <event-id> --calendar Work --title "Updated title"`
- `calendar delete <event-id> --confirm`

Use `calendar accounts` first if you are unsure which account to target.
Use `--description-file /path.md` for long descriptions.
Recurring events are visible in read commands, but update/delete only work on non-recurring events in v1.
{account_note}Use `--output /path.json` or `--output /path.md` on read commands when you need a reusable export.
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

        skills["browse.md"] = """# Browse

Interactive browser reading is exposed through `browse` commands.

Useful commands:
- `browse open https://example.com`
- `browse open --result 1`
- `browse current`
- `browse back`
- `browse text`
- `browse text > /page.txt`
- `browse links --absolute`
- `browse links --absolute --output /links.json`
- `browse elements "a" --attr href --attr innerText`
- `browse click "button.submit"`

The browser session only exists for the current run. It does not persist across later thread messages.
Use `--output` when you want to keep extracted text, links, or elements in the filesystem.
Use `curl` or `wget` when you need direct downloads rather than page interaction.
"""

    if capabilities.has_search:
        browse_note = (
            "\nUse `search` to discover candidate pages, then open a result during the same run with:\n"
            "- `browse open --result 1`\n"
            if capabilities.has_web
            else ""
        )
        skills["search.md"] = f"""# Search

Web search is exposed through the `search` command.

Useful commands:
- `search climate summit`
- `search climate summit --limit 3`
- `search climate summit --output /search/results.json`
{browse_note}
`search` results do not persist across later thread messages unless you write them to a file with `--output`.
"""

    if capabilities.has_mcp:
        skills["mcp.md"] = """# MCP

Remote MCP servers are exposed through the `mcp` command family.

Useful commands:
- `mcp servers`
- `mcp tools --server 12`
- `mcp schema list_pages --server Notion MCP`
- `mcp call list_pages --server Notion MCP query=\"roadmap\"`
- `mcp call list_pages --server Notion MCP < /tmp/input.json`
- `mcp call export_report --server Reports --extract-to /reports`

Use `mcp schema` before `mcp call` when you do not already know the expected JSON input shape.
When commands are piped or redirected, MCP emits normalized JSON to stdout.
Use `--output` to save the normalized result, or `--extract-to` when the tool returns files or resources.
"""

    if capabilities.has_api:
        skills["api.md"] = """# API

Configured custom API services are exposed through the `api` command family.

Useful commands:
- `api services`
- `api operations --service CRM`
- `api schema create_invoice --service Billing`
- `api call create_invoice --service Billing customer_id=42 amount=199`
- `api call create_invoice --service Billing < /tmp/payload.json`
- `api call export_pdf --service Billing --output /tmp/invoice.pdf`

Each API service exposes declared operations with a fixed method, path template, and JSON schema.
Use `api schema` before `api call` when you need the exact expected payload shape.
When commands are piped or redirected, API commands emit normalized JSON to stdout.
Binary API responses must be saved with `--output`.
"""

    if capabilities.has_webdav:
        skills["webdav.md"] = """# WebDAV

Remote WebDAV storage is exposed as a filesystem mount under `/webdav`.

Start with:
- `ls /webdav`
- `ls /webdav/<mount>`
- `cat /webdav/<mount>/notes.txt`
- `cp /report.txt /webdav/<mount>/report.txt`
- `cp /webdav/<mount>/report.txt /report.txt`
- `tee /webdav/<mount>/notes.txt --text "hello"`
- `mkdir /webdav/<mount>/archive`
- `mv /webdav/<mount>/draft.txt /webdav/<mount>/archive/draft.txt`
- `rm /webdav/<mount>/old.txt`

Use the normal filesystem commands rather than expecting dedicated WebDAV commands.
Whether writes, moves, copies, or deletes succeed depends on the permissions flags configured on the WebDAV tool.
"""

    if capabilities.has_webapp:
        skills["webapp.md"] = """# WebApp

Static webapps are authored directly in the normal terminal filesystem, then published live.

Useful commands:
- `mkdir /webapps/demo`
- `tee /webapps/demo/index.html --text "<!doctype html>..."`
- `tee /webapps/demo/styles.css --text "body { ... }"`
- `webapp expose /webapps/demo --name "Demo App"`
- `webapp show <slug>`
- `webapp list`
- `webapp delete <slug> --confirm`

`webapp expose` creates a live publication tied to the source directory.
After that, keep editing the files in the source directory normally with `tee`, `touch`, `mv`, `rm`, and `mkdir`.
The published app reflects those file changes automatically.
"""

    if capabilities.has_memory:
        skills["memory.md"] = """# Memory

Long-term memory is mounted as `/memory` and is shared at the user level.

Useful commands:
- `ls /memory`
- `ls -l /memory`
- `mkdir /memory/projects`
- `cat /memory/note.md`
- `cat /memory/projects/client-a.md`
- `grep -r "term" /memory`
- `cat /memory/projects/client-a.md | grep Constraints`
- `memory search "conceptual query"`
- `memory search "conceptual query" --under /memory/projects`
- `tee /memory/projects/client-a.md --text "# Client A\\n\\n## Constraints\\n..."`

Use any directory structure that helps the task; `/memory` does not impose themes or types.
Use `grep` for lexical text matching on visible memory documents.
Use `memory search` when you need semantic retrieval or hybrid lexical + embeddings ranking.
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
