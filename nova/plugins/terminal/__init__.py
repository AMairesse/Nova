from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor


def _skill_docs(_capabilities, _thread_mode):
    return {
        "terminal.md": """# Terminal

The main action surface is the persistent terminal.

Start by checking `pwd`, `ls /`, and `ls /skills` when you need orientation.

Files added from the Files panel live under `/`.
Use `/inbox` only for files attached to the current user message.
Use `/history` only for earlier message attachments.

Examples:
- `ls /`
- `mkdir -p /memory/preferences`
- `mkdir -p /tmp/demo; ls -l /tmp/demo`
- `echo "hello" > /note.txt`
- `find / -name "*.pdf"`
- `printf "b\na\nc\n" | sort`
- `ls -laR /subagents`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
Common Unix-like helpers such as `printf`, `sort`, `wc`, `file`, `rmdir`, `true`, `false`, recursive `ls -R`, and simple `ls` wildcards are supported.
Text pipes plus `<`, `>`, `>>`, `;`, `&&`, and `||` are supported, but this is not a full shell.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="terminal",
    label="Terminal",
    kind="system",
    skill_docs_provider=_skill_docs,
)
