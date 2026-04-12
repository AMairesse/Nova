# Continuous Discussion Mode

Last reviewed: 2026-04-06  
Status: implemented

## Scope

Continuous mode is a user-scoped discussion mode that coexists with classic thread mode.

Current building blocks:

- one continuous thread per user (`Thread.Mode.CONTINUOUS`)
- `DaySegment` rows for day boundaries
- stored day summaries
- `TranscriptChunk` and embeddings for recall
- runtime-native commands:
  - `history search`
  - `history get`

## Runtime Behavior

### Thread and day lifecycle

- `ensure_continuous_thread(user)` guarantees exactly one continuous thread
- a new `DaySegment` opens on the first user message of the day
- when a new day starts, Nova can enqueue summary refresh for the previous day
- transcript indexing is refreshed after new messages

### Context loaded into the runtime

`load_continuous_context(...)` builds runtime context from persisted data only:

- the previous available day summaries, under a strict token budget
- today’s partial summary when present
- the current-day raw message window after the summary boundary

This logic relies only on persisted messages, summaries, and transcript chunks.

### Recall commands

`history search`:

- searches day summaries and transcript chunks
- uses hybrid lexical + semantic retrieval when embeddings are available
- degrades to lexical-only search otherwise

`history get`:

- fetches a day summary
- or fetches a centered/ranged message window

### Continuous summaries

Day summaries are persisted Markdown, used for:

- context loading
- user-visible day summaries in the continuous UI
- search relevance when historical context is needed

## Background Tasks

Implemented tasks include:

- transcript indexing / append indexing
- day summary generation
- nightly continuous summary maintenance

The per-user maintenance task remains auto-managed and visible in Tasks.

## User-Facing Endpoints

- `continuous_home`
- `continuous_days`
- `continuous_day`
- `continuous_messages`
- `continuous_add_message`
- `continuous_regenerate_summary`

## Key Current Guarantees

- continuous mode and classic threads coexist
- continuous context is rebuilt directly from persisted DB state
- recall commands are only exposed when they make sense for the current continuous run
- summary refreshes publish realtime updates to the continuous page
