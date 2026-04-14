from __future__ import annotations

from nova.plugins.registry import get_internal_plugins

from .capabilities import TerminalCapabilities


def build_skill_registry(
    capabilities: TerminalCapabilities,
    *,
    thread_mode: str | None = None,
) -> dict[str, str]:
    skills: dict[str, str] = {}

    for plugin in get_internal_plugins():
        if plugin.plugin_id not in capabilities.plugins:
            continue
        skills.update(plugin.build_skill_docs(capabilities, thread_mode=thread_mode))

    if capabilities.has_subagents:
        skills["subagents.md"] = """# Sub-agents

Sub-agents are delegated through the dedicated `delegate_to_agent` tool, not
through terminal commands.

Use sub-agents when a specialized configured agent can handle a focused task.
Pass terminal file paths in `input_paths` when the child agent needs local files.
The child agent receives copied inputs under `/inbox`.
The child runs in its own isolated workspace.
Do not delegate thread-scoped cleanup, file organization, or webapp publication to a child agent.
Only files written in the child persistent `/` workspace are copied back automatically under
`/subagents/<subagent-slug>-<run-id>/`; files left in the child `/tmp` do not come back.
If you want to build or repair a webapp from child output, copy the returned files into the
main thread workspace yourself, then run `webapp expose` or other webapp commands in the
main agent.
"""

    return skills
