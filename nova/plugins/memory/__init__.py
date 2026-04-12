from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool


def _skill_docs(_capabilities, _thread_mode):
    return {
        "memory.md": """# Memory

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
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="memory",
    label="Memory",
    kind="builtin",
    builtin_subtypes=("memory",),
    command_families=("memory",),
    settings_metadata={
        "name": "Memory",
        "description": "Store reusable notes, preferences, and long-term knowledge that your agents can use later.",
        "requires_config": False,
        "config_fields": [],
        "settings_route_name": "user_settings:dashboard",
        "settings_anchor": "#pane-memory",
        "settings_label": _("Open memory settings"),
    },
    runtime_capability_resolver=resolve_single_builtin_tool("memory"),
    skill_docs_provider=_skill_docs,
    python_path="nova.plugins.memory",
    legacy_python_paths=("nova.plugins.memory",),
    catalog_section="built_in_capabilities",
    selection_mode="toggle",
    provisioning_sources=("system_default",),
    show_in_add_flow=False,
    default_enabled_for_primary_agents=True,
)
