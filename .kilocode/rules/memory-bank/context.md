# Current Context

## Current Work Focus

**In progress: continuous discussion mode (spec + macro-design in progress)**

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

Notes / remaining:

- UX/UI verification ongoing; migrations + full test suite intentionally deferred until the UI stabilizes.
