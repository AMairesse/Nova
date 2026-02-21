# Skill Middleware Mail (Tool-Based) - Nova

Date: 2026-02-19  
Status: Planned  
Scope: Architecture + runtime contracts + test plan

## 1. Objective

Implement Mail as an on-demand skill for Nova tool-based agents, without loading Mail tools by default in model context.

## 2. Standards Alignment

This design aligns with Agent Skills for tool-based agents:
- skills discovered as metadata
- explicit skill activation
- progressive disclosure of instructions/resources

References:
- https://agentskills.io/integrate-skills
- https://agentskills.io/specification

## 3. V1 Design Decisions

1. Skill classification source of truth:
- Builtin module `METADATA`, not database fields.

2. Skill instructions source:
- V1 keeps instructions in code (non-intrusive):
  - `get_skill_instructions(...)` in the builtin module.
- SKILL.md filesystem packaging is deferred to V2.

3. User configuration:
- No UI change to edit skill internals.
- User still only assigns tools to agents.
- Skill internals are read-only from user perspective.

4. Activation model:
- Explicit activation via `load_skill("mail")`.

5. Activation lifetime:
- Current turn only (ephemeral).

6. Scope:
- Builtin tools only in V1.

7. Email aggregation:
- Keep existing multi-mailbox aggregation exactly as-is.

## 4. Runtime Contracts

## 4.1 Builtin metadata extension

Example in `nova/tools/builtins/email.py`:

```python
METADATA = {
    ...,
    "loading": {
        "mode": "skill",        # "always" | "skill"
        "skill_id": "mail",
        "skill_label": "Mail",
    },
}
```

Default behavior when `loading` absent:
- `mode="always"`.

## 4.2 Optional instructions provider (module-level)

```python
def get_skill_instructions(agent=None, tools=None) -> str | list[str]:
    ...
```

Purpose:
- Provide Mail-specific operating policy only when skill is activated.

## 4.3 Control tools exposed by runtime

- `list_skills()`
- `load_skill(skill: str)`

Behavior:
- If no skills exist for an agent, control tools may be omitted.
- If `load_skill("mail")` succeeds, Mail tools become visible only for the current turn.

## 5. Agent Execution Flow (Mail request)

1. User asks for an email task.
2. First model call sees:
- regular tools
- `list_skills` / `load_skill`
- Mail tools hidden
3. Model calls `load_skill("mail")`.
4. Next model call (same turn) sees Mail tools.
5. Model performs iterative Mail tool calls (list/read/search/move/send/draft).
6. Agent returns final answer.
7. Next user turn starts with Mail tools hidden again.

## 6. Compatibility Requirements

1. Preserve existing email aggregation path:
- `AGGREGATION_SPEC`
- `get_aggregated_functions(...)`
- `get_aggregated_prompt_instructions(...)`

2. Do not regress single-mailbox legacy names.

3. Do not change bootstrap behavior in V1.

4. Do not add DB migrations in V1.

## 7. Security and Safety

- Skill activation is explicit.
- Sending safeguards remain unchanged.
- Keep existing tool error middleware behavior.
- Log skill activation events for traceability.

## 8. Implementation Outline

1. Add skill policy parsing from builtin `METADATA`.
2. Add runtime control tools `list_skills` and `load_skill`.
3. Add model-call middleware filtering visible tools by active skills.
4. Inject skill instructions only after activation.
5. Keep existing aggregation untouched.

## 9. Test Matrix

1. Visibility:
- Mail tools hidden before activation.
- Mail tools visible after `load_skill("mail")` in same turn.
- Mail tools hidden again next turn.

2. Activation:
- Unknown skill id returns explicit error.

3. Aggregation:
- Multi-mailbox selector works exactly as today post-activation.

4. Safety:
- Sending blocked when mailbox sending is disabled.

5. Regression:
- Non-skill builtins unaffected.

## 10. V2 (Deferred)

- Move inline instructions to filesystem `SKILL.md` per skill directory.
- Add assets/references tooling (`get_skill_asset(...)`) if needed.
