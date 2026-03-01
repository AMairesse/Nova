# Spam Filter Template: Mailbox Selection and Locking (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This document covers the predefined spam-filter task (`email_spam_filter_basic`) onboarding flow.

## Implemented flow

1. User clicks `Use this template`.
2. Nova redirects to mailbox selection (`task_template_select_mailbox`).
3. User chooses one valid `agent_id:email_tool_id` pair.
4. Nova pre-fills task creation form and stores a lock for the selected mailbox.
5. In the create form, `email_tool` is disabled and cannot be changed.

The prefill includes mailbox-aware values (notably task name and prompt) and `run_mode=ephemeral`.

## Mailbox option resolution

Availability is computed from real user setup:
- configured mailbox credentials must exist
- selectable agent must exist
- agent must have access to mailbox tool (directly or via sub-agent)

If prerequisites are not met, template flow is blocked with a user-facing error.

## Server-side guardrails

Locking is enforced server-side, not only in UI:
- selected mailbox id is stored in session (`task_template_lock_email_tool_id`)
- `TaskDefinitionForm.clean()` validates posted `email_tool` against lock
- tampered payloads are rejected and cannot switch mailbox silently

## Why this remains the chosen behavior

- Keeps task name/prompt/tool coherent.
- Avoids accidental mailbox drift after template prefill.
- Preserves explicit user intent and predictable execution.
