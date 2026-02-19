# Tasks Documentation

## Repetitive Workflow: Promote a builtin tool to on-demand skill

1. Add `METADATA.loading` in the builtin module.
2. Add/maintain `get_skill_instructions(...)` for activation-time guidance.
3. Ensure runtime registers skill in catalog.
4. Ensure `load_skill(<id>)` exposes tools only for current turn.
5. Validate aggregated and non-aggregated tool behavior remains intact.
6. Add/update tests:
- visibility before/after activation
- unknown skill errors
- aggregation compatibility
- safety constraints unchanged
