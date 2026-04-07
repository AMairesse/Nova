from __future__ import annotations

from nova.models.Thread import Thread
from nova.plugins.base import InternalPluginDescriptor


def _skill_docs(_capabilities, thread_mode):
    if thread_mode != Thread.Mode.CONTINUOUS:
        return {}
    return {
        "continuous.md": """# Continuous Mode

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
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="history",
    label="History",
    kind="system",
    skill_docs_provider=_skill_docs,
)
