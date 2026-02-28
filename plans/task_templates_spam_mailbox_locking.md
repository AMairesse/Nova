# Spam filtering template: mailbox selection and locking

## Decision

For the predefined spam-filtering task, mailbox selection is done **before** opening the task creation form.

In the creation form, the selected mailbox tool is **locked** (read-only) to avoid drift between:

- task title
- prefilled prompt content
- selected email tool

## UX flow

1. User clicks `Use this template` for spam filtering.
2. User is redirected to a mailbox selection step.
3. User picks one mailbox+agent combination.
4. User lands on create-task form with:
   - prefilled title/prompt matching that mailbox
   - mailbox field locked
5. If user wants a different mailbox, they must restart from template selection.

## Guardrails

- UI: disabled mailbox field + info message.
- Server: form validation enforces the locked mailbox, so tampered POST payloads cannot switch tool.

## Why this approach

- Keeps prompt/title/tool coherent.
- Avoids hidden side effects in-form.
- Makes intent explicit and predictable for users.
