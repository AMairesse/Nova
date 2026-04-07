from __future__ import annotations

from nova.plugins.base import InternalPluginDescriptor, resolve_external_tool_type


def _skill_docs(_capabilities, _thread_mode):
    return {
        "mcp.md": """# MCP

Remote MCP servers are exposed through the `mcp` command family.

Useful commands:
- `mcp servers`
- `mcp tools --server 12`
- `mcp schema list_pages --server Notion MCP`
- `mcp call list_pages --server Notion MCP query=\"roadmap\"`
- `mcp call list_pages --server Notion MCP < /tmp/input.json`
- `mcp call export_report --server Reports --extract-to /reports`

Use `mcp schema` before `mcp call` when you do not already know the expected JSON input shape.
When commands are piped or redirected, MCP emits normalized JSON to stdout.
Use `--output` to save the normalized result, or `--extract-to` when the tool returns files or resources.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="mcp",
    label="MCP",
    kind="external_adapter",
    tool_types=("mcp",),
    command_families=("mcp",),
    runtime_capability_resolver=resolve_external_tool_type("mcp"),
    skill_docs_provider=_skill_docs,
)
