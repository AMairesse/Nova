from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor


def _skill_docs(_capabilities, _thread_mode):
    return {
        "terminal.md": """# Terminal

The main action surface is the persistent Nova terminal.

Start with:
- `pwd`
- `ls -la`
- `ls /skills`
- `echo "hello" > /note.txt`
- `cat /note.txt | grep hello`
- `cat -n /note.txt | tail -1`
- `grep -in hello /note.txt | head -5`
- `wc -l /note.txt`
- `rm -f /note.txt`
- `tee /note.txt --text "first line\\nsecond line"`

Use relative paths only if you are confident about the current working directory.
If you are unsure, run `pwd` first.
Minimal text pipes plus `<`, `>`, and `>>` are supported, but this is not a full shell.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="terminal",
    label="Terminal",
    kind="system",
    skill_docs_provider=_skill_docs,
)
