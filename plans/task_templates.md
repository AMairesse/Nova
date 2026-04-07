# Task Templates

Last reviewed: 2026-04-06  
Status: implemented

## Scope

This file documents the predefined task templates and their current prerequisites.

Current template ids:

- `email_spam_filter_basic`
- `thematic_watch_weekly`

## Shared Mechanics

Template availability is computed per user by `template_registry`.

Apply flow:

- unavailable template -> redirect with user-visible error
- available template -> prefill payload stored in session, then open task creation form

## Spam Filter Template

Id: `email_spam_filter_basic`

Availability requirements:

- at least one configured mailbox credential
- at least one selectable agent
- at least one valid `(agent, email_tool)` pair

Flow:

1. mailbox selection
2. prefill generation from the selected mailbox
3. creation form with locked `email_tool`

Prefill contract:

- trigger: `email_poll`
- run mode: `ephemeral`
- mailbox-aware name and prompt
- polling interval default: 5 minutes
- locked mailbox in the creation form

Detailed locking behavior is documented in `task_templates_spam_mailbox_locking.md`.

## Thematic Watch Template

Id: `thematic_watch_weekly`

Availability requirements:

- at least one selectable agent
- one agent able to use both browser and memory, directly or through sub-agents

Tracked memory documents:

- `/memory/thematic-watch-topics.md`
- `/memory/thematic-watch-language.md`

Prefill contract:

- trigger: `cron`
- run mode: `new_thread`
- default schedule: Monday 06:00 UTC
- prompt instructs a weekly thematic watch and references the two memory documents above

## Guided Setup Path

`task_template_setup` for thematic watch:

- requires the default agent to have memory access
- redirects the user into a guided chat
- asks the runtime to create exactly two memory files:
  - topics file
  - language file

## Validation and Safety

Server-side validation remains authoritative:

- template availability is rechecked before form prefill
- locked mailbox cannot be overridden by tampered form data
- maintenance tasks remain separate from editable user-defined templates
