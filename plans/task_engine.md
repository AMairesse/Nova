# Task Engine

Last reviewed: 2026-04-06  
Status: implemented

## Scope

This document describes the current execution model for scheduled and interactive agent tasks.

## Core Models

- `TaskDefinition`: scheduler config and execution intent
- `Task`: runtime execution state (`PENDING`, `RUNNING`, `AWAITING_INPUT`, `COMPLETED`, `FAILED`)
- `Interaction`: blocking clarification during a task run

`TaskDefinition` supports:

- `task_kind`: `agent` or `maintenance`
- `trigger_type`: `cron` or `email_poll`
- `run_mode`: `new_thread`, `continuous_message`, `ephemeral`

## Scheduling

`TaskDefinition.save()` keeps its `django_celery_beat` task in sync:

- `cron` trigger -> `CrontabSchedule`
- `email_poll` trigger -> `IntervalSchedule`

## Execution Path

Agent tasks run through the current runtime/task executors:

- `ReactTerminalTaskExecutor`
- `ReactTerminalSummarizationTaskExecutor`

High-level flow:

1. render prompt variables
2. create the target thread/message based on `run_mode`
3. create a `Task`
4. execute through the React Terminal runtime
5. delete ephemeral threads in `finally` when required

## Run Modes

### `new_thread`

- creates a new classic thread
- adds the prompt as a user message

### `continuous_message`

- appends to the user’s continuous thread
- triggers continuous follow-up indexing/summarization hooks

### `ephemeral`

- creates a temporary classic thread
- runs the task
- deletes the thread afterwards

## Interactive Clarification

When the runtime calls `ask_user(...)`:

- task status becomes `AWAITING_INPUT`
- an `Interaction` row is created
- websocket event `user_prompt` is emitted
- user answers/cancels through the same interaction endpoints
- resume is performed through the runtime-aware resume path

The resume path rehydrates the runtime context and injects a synthetic tool result for `ask_user`.

## Continuous Hooks

Continuous-specific task execution:

- appends messages through the continuous helpers
- relies on stored summaries and transcript chunks

## Trigger Tasks

Implemented trigger tasks include:

- cron task execution
- email polling task execution
- maintenance task execution

Trigger-driven runners use bounded exponential retry.
