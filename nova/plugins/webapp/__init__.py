from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool


def _skill_docs(_capabilities, _thread_mode):
    return {
        "webapp.md": """# WebApp

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
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="webapp",
    label="WebApp",
    kind="builtin",
    builtin_subtypes=("webapp",),
    command_families=("webapp",),
    settings_metadata={
        "name": "WebApp",
        "description": "Expose a live static webapp from a terminal source directory.",
        "loading": {
            "mode": "skill",
            "skill_id": "webapp",
            "skill_label": "WebApp",
        },
        "requires_config": False,
        "config_fields": [],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("webapp"),
    skill_docs_provider=_skill_docs,
    python_path="nova.plugins.webapp",
    legacy_python_paths=("nova.plugins.webapp",),
    catalog_section="built_in_capabilities",
    selection_mode="toggle",
    provisioning_sources=("system_default",),
    show_in_add_flow=False,
    default_enabled_for_primary_agents=True,
)
