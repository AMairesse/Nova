# Task Templates (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This file documents predefined task templates and their runtime prerequisites.

Current template ids:
- `email_spam_filter_basic`
- `thematic_watch_weekly`

## Shared mechanics

Template availability is computed per user by `template_registry`.

Template apply flow:
- unavailable template -> redirect with user-visible error
- available template -> prefill payload stored in session, then task creation form

## Spam filter template

Id: `email_spam_filter_basic`

Availability requirements:
- at least one configured mailbox credential
- at least one selectable agent
- at least one selectable `(agent, email_tool)` pair (direct or via sub-agent)

Flow:
1. dedicated mailbox selection step
2. prefill generation using selected mailbox
3. creation form with locked `email_tool`

Prefill contract:
- trigger: `email_poll`
- run mode: `ephemeral`
- mailbox-aware name and prompt
- polling interval default: 5 min
- `lock_email_tool=true`

Detailed mailbox locking behavior is documented in `task_templates_spam_mailbox_locking.md`.

## Thematic watch template

Id: `thematic_watch_weekly`

Availability requirements:
- at least one selectable agent
- one agent able to use both browser and memory (directly or via sub-agent)

Memory items for topics/language are tracked as prerequisites in UI state, but current availability is not hard-blocked by their absence.

Prefill contract:
- trigger: `cron`
- cron: `0 6 * * 1` (Monday 06:00 UTC)
- run mode: `new_thread`
- prompt instructs weekly web watch and memory retrieval fallback behavior

## Guided setup path (thematic watch)

`task_template_setup`:
- requires default agent to have memory tool access
- redirects to continuous chat with a structured onboarding prompt
- asks agent to store exactly two memory items:
  - `theme='thematic_watch_topics'`, `type='preference'`
  - `theme='thematic_watch_language'`, `type='preference'`

## Validation and safety

Server-side validation remains authoritative:
- template-specific selection validity is rechecked before form prefill
- locked mailbox cannot be overridden by tampered form payload
- maintenance tasks are separate from templates and not editable/deletable from user task forms
