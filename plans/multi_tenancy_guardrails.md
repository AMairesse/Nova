# Multi-tenancy Guardrails (Implemented Patterns)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This document summarizes guardrails currently implemented to keep user data isolated.

## Primary isolation model

Most domain entities are user-scoped via FK to user and are queried with explicit user filters.

Examples of user-scoped runtime entities:
- threads/messages/tasks/interactions
- memory items/themes/embeddings
- day segments/transcript chunks/conversation embeddings
- task definitions and runtime state

## View-layer ownership checks

HTTP views commonly enforce ownership with `get_object_or_404(..., user=request.user)` or explicit checks.

Implemented examples:
- thread and message actions
- task lists/running task lookup
- interaction answer/cancel APIs
- continuous mode day/message endpoints

## Tool-layer scoping

Builtin tools use `agent.user` to scope reads/writes.

Implemented examples:
- memory tool queries filter by `user=agent.user`
- conversation recall filters day segments/messages/chunks by `user=agent.user`

## Tool credential isolation

Tool credentials are user-bound (`ToolCredential.user`) even when tools may be system-level (`Tool.user is null`).

Implication:
- a shared system tool can exist
- each user still uses their own credential row

## Agent profile constraints

`UserProfile.default_agent` is validated to:
- belong to the same user
- not be a sub-agent (`is_tool=True`)

## Task/interaction safeguards

Interaction endpoints verify ownership before state changes:
- task owner must match authenticated user
- thread owner must match authenticated user

## Operational review checklist for new code

When adding new features, verify:
- every ORM query touching user data is user-scoped
- object fetch endpoints enforce ownership before mutation
- any cross-entity link keeps same-user consistency
- background tasks receiving IDs are only triggered from trusted, user-validated entrypoints

## Known boundary

Internal Celery tasks generally trust DB ids received from validated application flows. Keep that assumption explicit when introducing new asynchronous entrypoints.
