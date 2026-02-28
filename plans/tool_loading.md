# Tool Loading Pipeline (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This document covers how Nova builds the effective tool list for an agent run.

## High-level pipeline

`LLMAgent.create(...)` -> `load_tools(agent)` -> `create_agent(...)` with middleware.

Tool sources:
- builtin tools assigned to agent
- MCP tools
- agent-as-tool wrappers (sub-agents)
- files tools (always loaded via `nova.tools.files`)
- runtime skill control tools (`list_skills`, `load_skill`)

## Builtin loading

- Builtins are grouped by module `python_path`.
- Module import is restricted by whitelist (`nova.tools.builtins.*`).
- Optional aggregation path is used when module exposes `get_aggregated_functions` and threshold is met.
- Otherwise, per-tool `get_functions(tool, agent)` is used.

Prompt hints are collected from:
- `get_prompt_instructions`
- `get_aggregated_prompt_instructions` (when aggregation is used)

## Skill-aware loading

Skill policy comes from module `METADATA.loading`.

When `mode=skill`:
- tools are tagged with skill metadata
- `agent.skill_catalog` is populated (label, tool names, instructions)
- skill-specific instructions are collected via `get_skill_instructions(...)`

After all loading, control tools are added:
- `list_skills`
- `load_skill`

Actual visible skill tools are filtered per-turn by middleware (`apply_skill_tool_filter`).

## Continuous conversation policy

Conversation recall tools are constrained as follows:
- only for main agent in continuous thread mode
- hidden for sub-agents (`agent_config.is_tool=True`) and non-continuous threads
- auto-attached if missing in a valid continuous main-agent run

## MCP and agent tools

MCP tools:
- use pre-fetched metadata/credentials when available
- tool names are sanitized for LangChain compatibility

Agent-as-tool:
- loaded through `AgentToolWrapper`
- appended to the same final tool set

## Name safety and runtime metadata

Before returning:
- tool names are globally deduplicated (`__dupN` suffix strategy)
- skill control tool names are recorded on agent runtime context
- prompt hints and skill catalog are attached to agent context

## Middleware integration

The final LangGraph/LangChain agent is built with middleware:
- dynamic system prompt (`nova_system_prompt`)
- skill tool filter (`apply_skill_tool_filter`)
- tool error handling (`handle_tool_errors`)
