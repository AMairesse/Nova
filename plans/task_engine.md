# Task Engine (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This document describes the implemented execution model for scheduled and interactive agent tasks.

## Core models

- `TaskDefinition`: scheduler config and execution intent.
- `Task`: runtime execution state (`PENDING`, `RUNNING`, `AWAITING_INPUT`, `COMPLETED`, `FAILED`).
- `Interaction`: blocking question/answer checkpoint during a task run.

`TaskDefinition` supports:
- `task_kind`: `agent` or `maintenance`
- `trigger_type`: `cron` or `email_poll`
- `run_mode`: `new_thread`, `continuous_message`, `ephemeral`

## Scheduling and Beat sync

`TaskDefinition.save()` keeps a `django_celery_beat` `PeriodicTask` in sync:
- `cron` trigger -> `CrontabSchedule`
- `email_poll` trigger -> `IntervalSchedule` (minutes)

Disabling a definition disables the associated periodic task.
Deleting a definition deletes the associated periodic task.

## Celery entrypoints

Implemented trigger tasks:
- `run_task_definition_cron`
- `poll_task_definition_email`
- `run_task_definition_maintenance`

## Agent execution path

`execute_agent_task_definition(...)` flow:
1. Render prompt placeholders (`{{ var }}`) from runtime variables.
2. Prepare execution thread/message from `run_mode`:
   - `new_thread`: create classic thread + user message.
   - `continuous_message`: append to continuous thread and enqueue continuous follow-ups.
   - `ephemeral`: create temporary classic thread + user message.
3. Create runtime `Task` and execute via `AgentTaskExecutor`.
4. For `ephemeral`, delete the temporary thread in `finally`.

## Interactive interruption/resume

When agent interrupts for user input:
- `TaskExecutor` creates an `Interaction` (`PENDING`) and a linked `interaction_question` message.
- Task transitions to `AWAITING_INPUT`.
- WS event `user_prompt` is emitted.

User response/cancel (`interaction_views`):
- verifies user ownership
- updates interaction status (`ANSWERED` or `CANCELED`)
- queues `resume_ai_task`

`resume_ai_task` re-enters the same task/thread/agent execution context.

## Continuous-specific execution hooks

During continuous runs:
- checkpoint context can be rebuilt before invocation (`ensure_continuous_checkpoint_state`).
- after success, sub-agent checkpoints for the thread are purged (main checkpoint kept).

## Email polling specifics

`poll_new_unseen_email_headers(...)` behavior:
- read-only IMAP polling
- UID cursor and UIDVALIDITY tracking in `TaskDefinition.runtime_state`
- first run processes unseen backlog
- backlog is skipped after prolonged downtime (>2x poll interval)

When new headers are found, prompt variables are injected (count + markdown/json header list).

## Retry behavior

Trigger-driven runners use bounded exponential retry:
- max retries: 5
- countdown: exponential backoff from 30s (capped)

Direct runtime tasks (`run_ai_task`, `resume_ai_task`, `summarize_thread_task`) use Celery retry-on-error behavior defined in task code.
