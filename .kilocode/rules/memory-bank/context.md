# Current Context

## Current Work Focus

**In progress: redesign long-term memory (structured + hybrid retrieval)**

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
