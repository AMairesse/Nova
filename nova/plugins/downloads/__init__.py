from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_downloads_from_browser


def _skill_docs(_capabilities, _thread_mode):
    return {
        "web.md": """# Web

Web downloads are exposed through familiar commands:

- `wget <url>`
- `wget <url> --output /downloads/file.ext`
- `curl <url>`
- `curl <url> --output /downloads/file.ext`

Use `curl` without `--output` only when you want a text preview.
Use `wget` or `curl --output` when you need a reusable file.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="downloads",
    label="Downloads",
    kind="system",
    command_families=("downloads",),
    runtime_capability_resolver=resolve_downloads_from_browser,
    skill_docs_provider=_skill_docs,
)
