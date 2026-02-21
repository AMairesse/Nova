# Current Context

## New Focus (2026-02-19): Tool-Based Skills Middleware (Mail)

- We are planning a V1 skills runtime for tool-based agents.
- Mail is the first builtin moved to on-demand skill mode.
- Source of truth for "normal tool" vs "skill tool" is builtin `METADATA.loading`.
- User settings remain unchanged for skill internals (read-only).
- Activation is explicit via `load_skill("mail")`.
- Activation lifetime is current turn only.
- Existing email aggregation behavior must be preserved.
- Reference plan: `/Users/toitoine/Documents/Nova/plans/skill_middleware_mail.md`.

## Current Work Focus

**In progress: continuous discussion mode (implementation + context/checkpoint semantics)**

- Goal: add a default “continuous discussion” mode that coexists with thread-based mode.
- Artifact: [`plans/continuous_discussion.md`](plans/continuous_discussion.md:1) (spec cleaned + enriched with ASCII mockups + Mermaid flows).
- V1 decisions captured in the spec:
  - exactly one continuous thread per user
  - visible day segments (boundary = first message of the day, user timezone)
  - bounded context: today raw window (4k token budget with aggressive tool-output trimming) + today summary + yesterday summary
  - new tools: `conversation.search` + `conversation.get`
  - `conversation.search` scope: summaries + transcript FTS + embeddings (when enabled), summaries-first, slight penalty for transcript hits covered by summary
  - daily summaries stored as Markdown and also emitted as system messages for web UI clarity
  - sub-agents stateless; `conversation.*` reserved to main agent
  - explicit policy to avoid cannibalization between global Memory v2 (`memory.*`) and conversation recall (`conversation.*`)

Status note:

- Long-term memory redesign (Memory v2) is considered **done**; current focus is **100%** on continuous discussion mode.

- Goal: replace current Markdown/theme-based memory (`UserInfo.markdown_content`) with structured memory items + themes.
- Models: `MemoryTheme`, `MemoryItem`, `MemoryItemEmbedding` (pgvector, 1024 dims).
- Retrieval:
  - Lexical: PostgreSQL FTS
  - Semantic: pgvector cosine distance when embeddings exist
  - Fallback: FTS-only when embeddings disabled or vectors not ready
- Embeddings:
  - computed asynchronously via Celery
  - provider selection precedence: system `llama.cpp` (if configured) → user-configured HTTP endpoint → disabled
- Embeddings dim handling:
  - DB stores a fixed-size pgvector (1024 dims)
  - embeddings shorter than 1024 are accepted and **zero-padded**; embeddings larger than 1024 are rejected
- Prompt:
  - do not inject memory content
  - only inject short instructions to use `memory.search` / `memory.get` / `memory.add`

Recent updates (since last compact)

- Tool semantics:
  - `memory.search` supports match-all: `query='*'` **or empty query** returns most recent items (subject to filters + `limit`).
  - `memory.add` defaults missing/blank theme to `general` to avoid “theme-less” items.
  - Added lifecycle tool: `memory.archive(item_id)` (soft delete).
  - `memory.search` filters `status='active'` by default; `status='any'` returns both active + archived.
- Data model simplification:
  - Removed `superseded` status and removed `MemoryItem.supersedes`.
  - `MemoryItem.status` is now only `active|archived`.
- UI (Memory settings → Memory browser):
  - Added “Include archived” toggle (default OFF).
  - Toggle triggers HTMX refresh of the memory list.
  - Fixed toggle bug where `include_archived=0` was treated as truthy and the switch bounced back to ON; now only `1/true/yes/on` are considered enabled.
- UI:
  - new “Memory settings” is repurposed to configure embeddings provider + includes a “Test embeddings endpoint” healthcheck
  - configuration stored at user-level (UserParameters)
  - read-only “Memory browser” table embedded under the settings form
  - config change confirmation: changing provider/model prompts confirmation and shows how many embeddings will be rebuilt

Design spec: [`plans/memory.md`](plans/memory.md)

## Continuous discussion mode – implementation status (recent)

Work completed/changed recently (post-spec cleanup):

- Continuous mode now has day-scoped message loading (`/continuous/messages/?day=YYYY-MM-DD`).
- Continuous UI day selector supports deep-linking via `?day=` and shows **“Today”** label for the current day.
- Day summary UI is rendered from `DaySegment.summary_markdown` and shows a “Day summary updated” event derived from `DaySegment.updated_at` (no persisted `system` Message for summary updates).
- Summary panel is injected into the same scroll container as the timeline, so it scrolls with the conversation.
- Removed browser persistence of selected thread (`lastThreadId` / `lastContinuousThreadId`) and related JS code paths.
- Threads UI now filters out the continuous thread (shows only `Thread.mode=thread`).
- Threads delete endpoint returns JSON (`{"status":"OK"}`) so deletion persists server-side when called via `fetch`.
- Fixed stuck “Running AI agent” UI after reload by explicitly re-enabling input and hiding the progress bar when `/running-tasks/<thread_id>/` returns no running tasks.

### Continuous context / checkpoints (implemented)

- Thread-mode auto-compaction (`SummarizationMiddleware` / `auto_summarize`) is disabled for `Thread.mode=continuous`.
- Continuous main-agent context is built by lazily rebuilding the LangGraph checkpoint from:
  - yesterday summary (if any)
  - today summary (if any)
  - today raw messages window
- Fingerprint-driven rebuild state is stored on `CheckpointLink` (`continuous_context_fingerprint`, `continuous_context_built_at`).
- Day summaries store a boundary pointer `DaySegment.summary_until_message` so when a day summary exists, only messages **after** the boundary remain in the raw window.
- Sub-agents are stateless in continuous: after each successful run, all sub-agent checkpoints for the thread are purged (all `CheckpointLink` except the main agent).

### Continuous conversation search (agent tools) – implemented

- `conversation.search` now supports hybrid retrieval (FTS + semantic vectors when available), with fallback to FTS-only when embeddings are disabled/unavailable.
- Added conversation-specific embedding models:
  - `DaySegmentEmbedding` (1:1 with `DaySegment`)
  - `TranscriptChunkEmbedding` (1:1 with `TranscriptChunk`)
- Added Celery tasks for conversation embeddings lifecycle:
  - `compute_day_segment_embedding`
  - `compute_transcript_chunk_embedding`
  - `rebuild_user_conversation_embeddings`
- Transcript indexing now enqueues/refreshes chunk embeddings when chunks are created or updated.
- Day summary generation now enqueues/refreshes day-segment summary embeddings after summary persistence.
- Added migration `0043_conversation_embeddings` to create embedding tables and PostgreSQL HNSW vector indexes.
- Added tests (`nova/tests/test_conversation_embeddings.py`) covering:
  - embedding compute tasks success path
  - `conversation.search` fallback behavior when embeddings are disabled

### Continuous conversation tools policy (runtime + code organization)

- Conversation recall tools are now treated as **implicit system capabilities** of continuous mode, not user-addable builtins.
- Implementation moved from `nova/tools/builtins/conversation.py` to `nova/continuous/tools/conversation_tools.py` so builtin discovery no longer proposes it in tool catalogs.
- `LLM` tool loading now imports conversation tools directly from the continuous module when `thread.mode=continuous` and `agent_config.is_tool=False`.

### Search mutualization (Memory + Continuous)

- Introduced shared hybrid-search utilities in `nova/llm/hybrid_search.py` for:
  - query-vector resolution (`resolve_query_vector`)
  - FTS saturation scoring
  - cosine-distance → semantic similarity
  - min/max bounds + normalization
  - semantic/FTS score blending
- Refactored `memory.search` to use the shared utilities while keeping Memory-specific filters and output unchanged.
- Refactored `conversation.search` to use the shared utilities while keeping conversation-specific ranking policy (summary/message source weighting) and output unchanged.

Notes / remaining:

- UX/UI verified by user.
- Migrations and full test suite validated using `--settings nova.settings_test` (SQLite).
