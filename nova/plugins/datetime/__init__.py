from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool


def _skill_docs(_capabilities, _thread_mode):
    return {
        "date.md": """# Date / Time

Use the native `date` command for current date and time:

- `date`
- `date -u`
- `date +%F`
- `date +%T`

For more advanced date arithmetic, use Python instead of expecting full GNU date support.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="datetime",
    label="Date / Time",
    kind="builtin",
    builtin_subtypes=("date",),
    command_families=("date",),
    settings_metadata={
        "name": "Date / Time",
        "description": "Manipulate dates and times",
        "requires_config": False,
        "config_fields": [],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("date"),
    skill_docs_provider=_skill_docs,
    python_path="nova.plugins.datetime",
    legacy_python_paths=("nova.plugins.datetime",),
)
