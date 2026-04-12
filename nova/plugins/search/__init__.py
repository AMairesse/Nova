from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool


def _skill_docs(capabilities, _thread_mode):
    browse_note = (
        "\nUse `search` to discover candidate pages, then open a result during the same run with:\n"
        "- `browse open --result 0`\n"
        if capabilities.has_web
        else ""
    )
    return {
        "search.md": f"""# Search

Web search is exposed through the `search` command.

Useful commands:
- `search climate summit`
- `search climate summit --limit 3`
- `search climate summit --output /search/results.json`
{browse_note}
`search` results do not persist across later thread messages unless you write them to a file with `--output`.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="search",
    label="Search",
    kind="builtin",
    builtin_subtypes=("searxng",),
    command_families=("search",),
    settings_metadata={
        "name": "SearXNG",
        "description": "Interact with a SearXNG server (search)",
        "requires_config": True,
        "config_fields": [
            {"name": "searxng_url", "type": "string", "label": _("URL SearXNG server"), "required": True},
            {"name": "num_results", "type": "integer", "label": _("Max results"), "required": False},
        ],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("searxng"),
    skill_docs_provider=_skill_docs,
    python_path="nova.plugins.search",
    legacy_python_paths=("nova.plugins.search",),
    catalog_section="backend_capabilities",
    selection_mode="single_backend",
    provisioning_sources=("deployment_default", "user_connection"),
    show_in_add_flow=True,
    add_label="Search backend",
)
