from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor


def _skill_docs(_capabilities, _thread_mode):
    return {
        "terminal.md": """# Terminal

The main action surface is the persistent Nova terminal.

Start by checking `pwd`, `ls`, and `ls /skills` when you need orientation.

Examples:
- `mkdir -p /memory/preferences`
- `echo "hello" > /note.txt`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
Text pipes plus `<`, `>`, and `>>` are supported, but this is not a full shell.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="terminal",
    label="Terminal",
    kind="system",
    skill_docs_provider=_skill_docs,
)
