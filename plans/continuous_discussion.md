# Continuous Discussion Mode (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

Continuous mode is a user-scoped discussion mode that coexists with classic thread mode.

Implemented components:
- One continuous thread per user (`Thread.Mode.CONTINUOUS`).
- Day segmentation (`DaySegment`) with optional day summaries.
- Conversation recall tools (`conversation_search`, `conversation_get`).
- Continuous checkpoint rebuild policy before agent execution.

## Runtime behavior

### 1. Thread and day lifecycle

- `ensure_continuous_thread(user)` guarantees exactly one continuous thread per user.
- A day segment opens on the first message of the user day (`append_continuous_user_message`).
- On message append, Nova enqueues transcript indexing for that day segment.
- If the message opens a new day, Nova also enqueues summarization for the previous day.

### 2. Context loaded into the agent

Before invocation in continuous mode, `TaskExecutor` calls `ensure_continuous_checkpoint_state(...)`.

`load_continuous_context(...)` rebuilds context with this policy:
- Previous two available day summaries (most recent first), with a strict shared token budget.
- Truncation notice if previous summaries exceed budget.
- Today summary (if it exists and has `summary_until_message`).
- Today raw messages after `summary_until_message` (or all today messages if no summary boundary exists).

A fingerprint is persisted on `CheckpointLink` to avoid unnecessary rebuilds.

### 3. Conversation recall tools

Tools:
- `conversation_search`
- `conversation_get`

Current behavior:
- Search runs on `DaySegment.summary_markdown` and `TranscriptChunk.content_text`.
- PostgreSQL path uses hybrid scoring (FTS + semantic) when query embeddings are available.
- Fallback path (e.g. SQLite tests) uses lexical `icontains`.
- `conversation_get` supports:
  - summary fetch by `day_segment_id`
  - centered windows around `message_id`
  - range fetch (`from_message_id` / `to_message_id`)
  - directional pagination (`before_message_id` / `after_message_id`).

### 4. Tool exposure policy

- `conversation_*` tools are only exposed for the main agent on a continuous thread.
- They are hidden for sub-agents (`agent_config.is_tool=True`) and non-continuous runs.
- If missing, they are auto-attached for the continuous main agent during tool loading.

### 5. Sub-agent checkpoint policy

After a successful continuous run, Nova purges LangGraph checkpoints for sub-agents in that thread and keeps the main agent checkpoint only.

## Background tasks and scheduling

Implemented tasks:
- `index_transcript_append_task`
- `summarize_day_segment_task`
- `nightly_summarize_continuous_daysegments_task`
- `nightly_summarize_continuous_daysegments_for_user_task`

Per-user maintenance task definition is auto-ensured:
- name: `Continuous: nightly day summaries`
- maintenance task key: `continuous_nightly_daysegment_summaries_for_user`
- default cron: `0 2 * * *` (UTC)

## User-facing endpoints (continuous UI)

- `continuous_home`
- `continuous_days`
- `continuous_day`
- `continuous_messages`
- `continuous_add_message`
- `continuous_regenerate_summary`

## Out of scope in this document

This file intentionally excludes speculative UX mockups and future-phase architecture proposals.
