from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_external_tool_type


def _skill_docs(_capabilities, _thread_mode):
    return {
        "api.md": """# API

Configured custom API services are exposed through the `api` command family.

Useful commands:
- `api services`
- `api operations --service CRM`
- `api schema create_invoice --service Billing`
- `api call create_invoice --service Billing customer_id=42 amount=199`
- `api call create_invoice --service Billing < /tmp/payload.json`
- `api call export_pdf --service Billing --output /tmp/invoice.pdf`

Each API service exposes declared operations with a fixed method, path template, and JSON schema.
Use `api schema` before `api call` when you need the exact expected payload shape.
When commands are piped or redirected, API commands emit normalized JSON to stdout.
Binary API responses must be saved with `--output`.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="api",
    label="API",
    kind="external_adapter",
    tool_types=("api",),
    command_families=("api",),
    runtime_capability_resolver=resolve_external_tool_type("api"),
    skill_docs_provider=_skill_docs,
)
