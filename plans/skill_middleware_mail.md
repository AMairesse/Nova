# Skill Middleware (Mail and Other Skill-Loaded Tools)

Last reviewed: 2026-02-28
Status: implemented

## Scope

Nova supports on-demand tool loading via skills. Mail was the initial target, and the mechanism is now generic.

## How skills are declared

Skill loading policy comes from module `METADATA.loading`:
- `mode: "skill"`
- `skill_id`
- `skill_label`

If `loading` is absent or invalid, tools are treated as always available (`mode: "always"`).

## Runtime flow

### 1. Tool loading

`load_tools(...)`:
- loads builtin modules
- tags each loaded tool with skill metadata when module policy is `skill`
- builds `agent.skill_catalog` (skill id, label, tool names, instructions)
- appends runtime control tools:
  - `list_skills`
  - `load_skill`

### 2. Activation scope

Skill activation is turn-scoped:
- active skills are inferred from `load_skill` tool results after the latest `HumanMessage`
- activation does not persist across user turns

### 3. Tool filtering

`apply_skill_tool_filter` keeps:
- always-on tools
- skill control tools (`list_skills`, `load_skill`)
- skill tools whose `skill_id` is active for current turn

Inactive skill tools are hidden from the model call.

## Prompt behavior

Prompt builder adds:
- list of available on-demand skills
- active skills for current turn
- skill-specific instructions only for active skills

Non-skill tool hints remain in the regular tool policy section.

## Mail-specific guarantees

Implemented guarantees for mail skill mode:
- mail tool metadata is declared as a skill (`skill_id=mail`)
- mailbox aggregation path remains in place (`get_aggregated_functions`)
- multi-mailbox and single-mailbox behavior remain supported
- skill instructions are provided by `get_skill_instructions(...)`

## Current skill-loaded modules

Skill-loaded modules currently include at least:
- Mail
- Files
- CalDav
- WebDav
- WebApp

(Exact list depends on installed builtin modules and metadata.)

## Out of scope in this document

This file intentionally omits speculative packaging formats and deferred skill asset systems.
