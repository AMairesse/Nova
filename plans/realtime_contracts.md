# Realtime Contracts (WebSocket + UI)

Last reviewed: 2026-02-28
Status: implemented

## Scope

This document captures currently implemented realtime contracts between backend task execution and frontend updates.

## WebSocket routes

- Task channel: `/ws/task/<task_id>/`
- File operations channel: `/ws/files/<thread_id>/`

Consumers:
- `TaskProgressConsumer`
- `FileProgressConsumer`

Both support ping/pong keepalive messages.

## Task update event envelope

Backend publishes to Channels group `task_<task_id>` with payload:
- `{"type": "task_update", "message": {...}}`

Client receives only `message` JSON.

## Task streaming events (main UI)

Handled by `streaming-manager.js`:
- `progress_update`
- `response_chunk`
- `context_consumption`
- `new_message`
- `task_complete`
- `task_error`
- `thread_subject_updated`
- `user_prompt`
- `interaction_update`
- `summarization_complete`
- `webapp_update`
- `webapp_public_url`

## Interaction-specific realtime flow

1. Backend sends `user_prompt` when task enters interrupt state.
2. User answers/cancels via HTTP endpoints.
3. Backend sends `interaction_update` while resuming.
4. Input area is re-enabled when interaction resolves/task completes/errors.

## Continuous summary realtime flow

Handled by `continuous-page.js` when regenerating day summary:
- `progress_update`
- `continuous_summary_ready`
- `task_complete`
- `task_error`

`continuous_summary_ready` is used to refresh the selected day summary and day list state.

## Running-task reconnection contract

`GET /tasks/running/<thread_id>/` returns active tasks (`RUNNING`/`AWAITING_INPUT`) with:
- `id`
- `status`
- `current_response`
- `last_progress`

Frontend can reconnect WS streams and restore partial response UI from this payload.

## Payload caveat

`task_error` payload shape is not fully uniform across producers:
- generic task handler commonly emits `{error, category}`
- continuous summary flow may emit `{message, category}`

Frontend currently handles both patterns in different pages.
