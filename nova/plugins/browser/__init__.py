from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool


def _skill_docs(_capabilities, _thread_mode):
    return {
        "browse.md": """# Browse

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
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="browser",
    label="Browser",
    kind="builtin",
    builtin_subtypes=("browser",),
    command_families=("browse",),
    settings_metadata={
        "name": "Browser",
        "description": "Browse and inspect web pages.",
        "requires_config": False,
        "config_fields": [],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("browser"),
    skill_docs_provider=_skill_docs,
    python_path="nova.plugins.browser",
    legacy_python_paths=("nova.plugins.browser",),
)
