# Memory in Nova (Spec Draft)

## 0. Objective

Replace the current basic memory (single Markdown blob + themes) with a **structured memory store** that is:

- global per user
- accessed via tools (so it does not bloat prompts)
- searchable via **hybrid retrieval**: PostgreSQL FTS + optional embeddings

Constraints for this iteration:

- no backward compatibility required
- no migration required
- human UI is read-only
- scheduled consolidation exists in Nova already (UI-managed) and will be specified/implemented later

## 0.1 Deliverables of this spec

This document will specify:

1. A concrete **data model** (Django models + indexes)
2. The **agent-facing tool API** (functions + inputs/outputs)
3. The **prompt injection contract** (what is injected, and why)
4. Minimal **read-only UI** requirements for inspection

## 0.2 Implementation status (living notes)

Implemented / in-progress:

- Postgres Docker switched to pgvector-enabled PG16 image.
- New models: `MemoryTheme`, `MemoryItem`, `MemoryItemEmbedding` with `VectorField(dimensions=1024)`.
- Builtin tool `memory` rewritten (v2): `search/add/get/list_themes`.
- Prompt injection updated to stop injecting memory content and to point the agent to tool-based retrieval.
- Embeddings plumbing introduced:
  - provider selection (`llama.cpp` auto-preferred, else user-configured HTTP endpoint)
  - Celery task skeleton to compute embeddings
- User configuration direction chosen: **Option A** (dedicated Memory settings in `user_settings/`, stored in `UserParameters`) + a “Test embeddings endpoint” button.

Recent implementation notes:

- **Embeddings provider config is now DB-backed per-user** (read on each call) and applies immediately.
- **Embeddings dimension mismatch**: vectors smaller than 1024 are now accepted via **zero-padding**; vectors larger than 1024 error.
- **Provider/model change flow**: Save triggers an inline confirmation (shows count of embeddings to rebuild), Confirm enqueues a background rebuild.
- **Read-only Memory browser**: added a simple table view (items + embedding state) rendered under the Memory settings form.
- **Memory browser archived toggle**: added an “Include archived” switch (default OFF) that refreshes the list via HTMX.
- **pgvector index**: added PostgreSQL-only HNSW cosine index migration for `MemoryItemEmbedding.vector`.

## 1. Current state (baseline)

What exists today:

- Memory tool implemented in [`nova/tools/builtins/memory.py`](nova/tools/builtins/memory.py).
- Data stored in a single per-user Markdown blob: `UserInfo.markdown_content` ([`nova/models/UserObjects.py`](nova/models/UserObjects.py:13)).
- Prompt injection lists themes and includes `global_user_preferences` when tool enabled ([`nova/llm/prompts.py`](nova/llm/prompts.py:56)).

Issues with current design:

- one large blob is hard to search and scale
- theme slicing/rewrite is O(n) text processing per operation
- memory retrieval is theme-based, not query-based

## 2. Decisions

- Scope: **global per user**, shared across agents; an agent only accesses memory if it has the memory tool enabled.
- Search: **hybrid from day 1**
  - lexical: PostgreSQL FTS (good for exact tokens: variable names, IDs)
  - semantic: embeddings (good for paraphrases)
- Embeddings computation: **async via Celery**, and search falls back to FTS when embeddings are missing.
- Embeddings provider is **optional**:
  - if configured (local `llama.cpp` / `Ollama` / remote API), we compute/store embeddings
  - otherwise: FTS-only mode
- UI: read-only
- Consolidation: later (via existing scheduled tasks UI), not part of this spec iteration

Additional UX/API decisions (based on early tests)

- `memory.search` should support a “match all” mode:
  - `query='*'` means “return the most recent items” (subject to `limit` and other filters)
  - empty query is also accepted and treated like `'*'`
- Themes are not mandatory for agents, but we avoid “theme-less” items in storage:
  - if `theme` is omitted on `memory.add`, the system assigns it to the `general` theme automatically
  - consequence: `memory.list_themes()` will always include at least `general` as soon as one item exists

Additional decision:

- Vector search backend: use **pgvector** in PostgreSQL from day 1.

Embeddings provider selection:

- Default path: support a **custom HTTP embeddings endpoint** (configurable).
- If a local `llama.cpp` docker service is detected and configured in Nova, it becomes the **default system embeddings provider** (similar to system LLM providers):
  - default and not modifiable (system-level)
  - used automatically when embeddings are enabled
- If no embeddings provider is configured/available: run in **FTS-only** mode.

## 3. Target design

### 3.1 Data model (Django-level)

We create a new app-level set of models under `nova/models/`.

#### 3.1.1 `MemoryTheme`

Purpose: lightweight grouping for filtering and for the agent to target memories.

Fields:

- `user` (FK → user)
- `slug` (string, normalized, indexed, unique per user)
- `display_name` (string)
- `description` (text, optional)
- `created_at`, `updated_at`

Indexes/constraints:

- Unique: `(user_id, slug)`

Notes:

- Themes are optional; `MemoryItem.theme` can be null.

#### 3.1.2 `MemoryItem`

Purpose: the atomic unit of long-term memory.

Fields:

- `user` (FK → user)
- `theme` (FK → `MemoryTheme`, nullable)
- `type` (enum)
  - `preference` (stable user preference)
  - `fact` (factual statement)
  - `instruction` (how the user wants Nova to behave)
  - `summary` (condensed/derived memory)
  - `other`
- `content` (text)
- `source_thread` (FK → `Thread`, nullable)
- `source_message` (FK → `Message`, nullable)
- `tags` (JSON list of strings, optional)
- `status` (enum)
  - `active`
  - `archived`
- `created_at`, `updated_at`

Search support:

- `content_tsv` (PostgreSQL `tsvector`) maintained on write for FTS
  - alternatively computed at query time if we want to avoid triggers; but stored tsvector is faster.

Indexes:

- `(user_id, created_at desc)`
- `(user_id, theme_id, created_at desc)`
- `(user_id, type)`
- GIN index on `content_tsv`

Notes:

- `source_message` is optional but gives traceability and allows better debugging.
- The system can default `status=active`.

#### 3.1.3 `MemoryEmbeddingProviderConfig` (optional)

Purpose: allow embeddings infra to be optional and configurable.

Stored as either:

- a new model, or
- fields on existing user/provider configuration.

Spec-level fields needed somewhere:

- `enabled` boolean
- `provider_type` enum: `llamacpp`, `ollama`, `openai`, `custom_http`
- `endpoint_url` (optional)
- `model` (string)
- `api_key` (optional, encrypted if stored)
- `dimensions` (int, optional)

Provider precedence rules:

1. If system `llama.cpp` embeddings provider is available → use it.
2. Else if user-configured HTTP endpoint exists → use it.
3. Else → embeddings disabled (FTS-only).

Notes:

- For v1 we can keep this minimal and rely on environment variables + per-user toggle.

#### 3.1.4 `MemoryItemEmbedding`

Purpose: store embedding state and vector for a memory item.

Fields:

- `user` (FK → user) (denormalized for fast filtering)
- `item` (OneToOne → `MemoryItem`) (or FK if we want multiple vectors per item)
- `provider_type` (enum)
- `model` (string)
- `dimensions` (int)
- `state` (enum): `pending` | `ready` | `error`
- `error` (text, nullable)
- `created_at`, `updated_at`

Vector storage (choose one):

Chosen: **pgvector** column `vector`.

Indexing strategy (implementation detail to confirm):

- Use `hnsw` if available (better recall/latency tradeoff), else `ivfflat`.
- Maintain one vector per `MemoryItem` (OneToOne), for the active embeddings provider.

Query pattern:

- `ORDER BY vector <-> :query_vector` (or cosine distance) with a `WHERE user_id = ...` filter.

Note:

- We still keep PostgreSQL FTS as a parallel signal for exact-token matches.

### 3.2 Tool API (agent-facing)

Agent-facing API should be retrieval-first and stable.

#### 3.2.1 `memory.search()`

Input:

- `query` (string)
- `limit` (int, default 10, max 50)
- `theme` (string slug or null)
- `types` (list of strings or null)
- `recency_days` (int or null)
- `status` (string or null)
  - default: `active`
  - allowed: `active|archived|any`

Output (JSON-serializable):

- `results`: list of
  - `id`
  - `theme` (slug or null)
  - `type`
  - `content_snippet` (truncated)
  - `created_at`
  - `score` (float)
  - `signals`: `{ fts: bool, semantic: bool }`

Behavior:

- Special-case “match all”:
  - if `query` is empty or equals `'*'`, return items ordered by recency (newest first) and apply `limit`, `theme`, `types`, `recency_days` filters.
- Otherwise: always compute lexical match (FTS).
- By default, search returns only `status=active` items.
  - When `status='any'`, it returns all statuses.
- If embeddings provider configured:
   - compute query embedding (in-process or via cached provider client)
   - compute semantic similarity for items with `state=ready`
   - combine rankings (hybrid)
- If provider configured but no embeddings ready yet:
   - return FTS-only results

Hybrid ranking (v1)

- Target weighting: **70% semantic** (pgvector) + **30% lexical** (PostgreSQL FTS).
- Practical approach:
  1) Retrieve candidates from both signals:
     - semantic top K (items with embeddings `state=ready`)
     - FTS top K (ranked by `SearchRank`)
  2) Union candidates, then compute a **normalized** score per signal on that candidate set.
  3) Final score = `0.7 * semantic_norm + 0.3 * fts_norm`.
  4) Tie-breakers:
     - newer `created_at` first
     - then stable ordering by `id`.

Normalization guidance

- Semantic signal is distance-based (lower is better). Convert to similarity, then normalize.
  - Example: `semantic_sim = 1 / (1 + distance)` then min-max normalize within the candidate set.
- FTS signal is already a score (higher is better). Min-max normalize within the candidate set.

Fallback rules

- If embeddings are disabled or query embedding fails: use FTS-only.
- If no FTS matches but semantic matches exist: return semantic-only results.
- If both signals are empty: return empty list.

Parameters (implementation constants)

- `K` (per-signal candidate size): default 50 (bounded).
- `limit` remains max 50 for tool output.

#### 3.2.2 `memory.add()`

Input:

- `type`
- `content`
- `theme` (optional)
- `tags` (optional)

Output:

- `{ id, status }` where `status` includes whether embedding is `pending`.

Behavior:

- Create `MemoryItem`.
- Theme handling:
  - If `theme` is omitted or blank: use theme `general` (create it if missing).
- If embeddings are enabled:
  - create/update `MemoryItemEmbedding(state=pending)`
  - enqueue Celery task to compute the embedding.

#### 3.2.3 `memory.get()`

Input: `item_id`

Output: full item fields + embedding state.

#### 3.2.4 `memory.archive()` (agent-facing)

Goal: allow removing bad/outdated items without hard deletion.

Input:

- `item_id` (int)

Output:

- `{ id, status='archived' }`

Behavior:

- set `status=archived`
- keep row for audit/debugging.

Rationale (why archive-only)

- Simpler tool surface for the agent.
- Avoids giving the agent the ability to rewrite history; instead it can:
  1) create a corrected item via `memory.add` (with proper theme)
  2) archive the old one via `memory.archive`

Implication

- `memory.search` should filter `status=active` by default (unless explicitly overridden).

Non-goal (v1)

- `memory.delete()` hard delete is intentionally not exposed to agents.
- `memory.update()` is intentionally not exposed to agents (agent should add + archive).

#### 3.2.5 `memory.list_themes()`

Returns list of `(slug, display_name)` for this user.

Notes:

- Themes are effectively “the set of themes that exist in storage”, not an exhaustive taxonomy.
- Because `memory.add` defaults to `general`, users should reliably see at least one theme once memory has items.

### 3.2.6 Embedding compute task (Celery)

We introduce a task:

- `compute_memory_item_embedding(item_id)`

Behavior:

- Load item content.
- If embeddings not enabled: no-op.
- Else call provider:
  - local `llama.cpp` when available
  - else remote API
- Store vector + set state to `ready` or `error`.

### 3.2.7 Embeddings lifecycle rules

This feature uses a **rebuild-total** strategy for simplicity.

#### Provider/model changes

When the embeddings provider or model changes (via the user settings flow):

- Set all existing `MemoryItemEmbedding` rows for the user to `state=pending`.
- Clear `error` fields.
- Update embedding metadata fields (`provider_type`, `model`, `dimensions`) to the new configuration (so search ignores stale configs).
- Enqueue a background rebuild job that recomputes vectors for **all items** (active + archived).

Rationale:

- Avoids keeping multiple embedding versions.
- Keeps runtime search logic simple (single provider/model per user).

#### Item creation

On [`memory.add()`](nova/tools/builtins/memory.py:1):

- If embeddings enabled: create/update `MemoryItemEmbedding(state=pending)` and enqueue compute.
- If embeddings disabled: do not create embeddings rows (or keep them but unused).

#### Item archival

On [`memory.archive()`](nova/tools/builtins/memory.py:1):

- Do not delete embeddings.
- Archived items are excluded from search by default; embeddings can still be rebuilt (rebuild-total includes them).

#### Failure handling

- If compute fails: set `state=error` and store an error string.
- Search must treat `error` as “no embedding available” and fall back to lexical-only scoring for that item.

### 3.3 Prompt injection contract

Nova must remain **tool-driven**: the agent should **not** receive raw memory item content in the system prompt. Instead, the prompt provides short operational guidance and lightweight discovery hints.

#### 3.3.1 Goals

- Keep prompts small, stable, and low-risk (avoid injecting potentially sensitive memory content).
- Encourage correct use of the memory tool surface.
- Improve discoverability of themes without injecting item content.

#### 3.3.2 What gets injected

1) **Minimal instructions** (always injected when memory tool is enabled)

- A short block (5–10 lines) describing when to use:
  - [`memory.search()`](nova/tools/builtins/memory.py:1)
  - [`memory.get()`](nova/tools/builtins/memory.py:1)
  - [`memory.add()`](nova/tools/builtins/memory.py:1)
  - [`memory.archive()`](nova/tools/builtins/memory.py:1)
- Mention match-all semantics:
  - `query='*'` or empty query returns the most recent items (subject to filters/limit).
- Mention lifecycle defaults:
  - default searches return `status=active`; `status='any'` includes archived.

2) **Hints block** (enabled by default)

- Inject a compact “themes overview”:
  - Top N themes (default N=10).
  - Include counts of **active** items per theme.
  - No item content, no per-item titles, no excerpts.

Illustrative format (not strict):

- Available memory themes (top 10):
  - general (42)
  - work (18)
  - preferences (9)

Hard rule:

- Never inject the full memory store.
- Never inject memory item content in the system prompt (only the tool results should carry content).

#### 3.3.3 When it is injected

- Inject into the **system prompt** at agent creation time (agent-level), so it remains stable and avoids prompt bloat.
- Refresh hints at most once per agent run (or per day) to avoid frequent DB calls.

#### 3.3.4 Size limits

- Target: <= ~1 KB for memory guidance + hints.
- Hard cap: <= ~2 KB.

If theme enumeration would exceed the cap:

- truncate to top N themes
- include a note to use [`memory.list_themes()`](nova/tools/builtins/memory.py:1)

#### 3.3.5 Failure / fallback behavior

- If memory is disabled for the user or a DB error occurs:
  - still inject a minimal block stating “memory tools unavailable” and instruct to proceed without them.
- If theme hints query fails:
  - omit hints; keep minimal instructions.

### 3.4 UI (read-only)

Minimum UI goal:

- list themes
- browse memory items (filter by theme/type)
- show embedding state (pending/ready/error) when embeddings enabled

Out of scope:

- editing memory content
- managing scheduled consolidation from this feature (already exists elsewhere)

## 4. Future work (explicitly not in this iteration)

- Memory consolidation via scheduled tasks (nightly/weekly) using Nova’s existing UI-based scheduler.
- Continuous discussion mode (single ongoing session across days) built on top of the same memory store.

## 5. Relevant code touchpoints

- Current tool: [`nova/tools/builtins/memory.py`](nova/tools/builtins/memory.py)
- Current storage: [`nova/models/UserObjects.py`](nova/models/UserObjects.py)
- Prompt injection: [`nova/llm/prompts.py`](nova/llm/prompts.py:56)
- Bootstrap tool creation: [`nova/bootstrap.py`](nova/bootstrap.py:173)

## 6. Implementation plan (next steps)

This section maps the remaining spec decisions to concrete code changes.

### 6.1 Prompt injection “hints” (theme counts)

Goal: update prompt injection to include **top N themes + active item counts**, without leaking item content.

Files:

- Implement in [`nova/llm/prompts.py:_get_user_memory()`](nova/llm/prompts.py:91)
  - Replace the current theme list query (`MemoryTheme.objects...`) with an annotated query counting active items.
  - Suggested query shape:
    - `MemoryTheme.objects.filter(user=user).annotate(active_count=Count('items', filter=Q(items__status='active')))`
  - Limit to top N=10, and respect the ~2KB cap (truncate).

Notes:

- Keep DB access in `sync_to_async(..., thread_sensitive=True)` for SQLite test stability.

### 6.2 Hybrid ranking: target 70% semantic / 30% FTS

Goal: make [`memory.search()`](nova/tools/builtins/memory.py:207) follow the spec weighting.

Current state (code):

- Postgres branch orders by distance asc, then FTS rank desc, then created_at desc.
- This is not an explicit 70/30 weighted rerank.

Implementation plan:

- In [`nova/tools/builtins/memory.py:search()`](nova/tools/builtins/memory.py:207), PostgreSQL path:
  1) Fetch candidate IDs:
     - `semantic_top_k`: items with embedding vector ready, ordered by cosine distance
     - `fts_top_k`: items ordered by SearchRank
  2) Union candidates and compute per-item signals (distance + rank) on that reduced set.
  3) Normalize signals across the candidate set and compute `final_score = 0.7*semantic + 0.3*fts`.
  4) Order by final_score desc, then created_at desc, then id.

Fallbacks:

- If no provider / no query vec: FTS-only.
- If semantic candidates empty but FTS has results: FTS-only.

### 6.3 Embeddings lifecycle: rebuild-total

Goal: ensure provider/model changes cause a full rebuild of all embeddings.

Files:

- Confirm existing flow in [`user_settings/views/memory.py:MemorySettingsView.post()`](user_settings/views/memory.py:68)
  - Already triggers [`rebuild_user_memory_embeddings_task()`](nova/tasks/memory_rebuild_tasks.py:1) after confirmation.
- Ensure [`nova/tasks/memory_rebuild_tasks.py`](nova/tasks/memory_rebuild_tasks.py:1) implements “set all pending + recompute all”.

### 6.4 Tests

Add/extend tests in [`nova/tests/test_memory.py`](nova/tests/test_memory.py:1):

- `search('*')` and empty query returns newest items.
- Default `status=active` filtering; `status='any'` includes archived.
- `add()` defaults missing theme to `general`.
- Ranking invariants (coarse):
  - when vectors present, semantic matches should outrank pure lexical matches in most cases (do not assert exact float score).

### 6.5 UI polish (optional)

If needed, extend [`user_settings/views/memory_browser.py`](user_settings/views/memory_browser.py:7) and the template to add:

- theme filter dropdown based on existing themes
- consistent archived badge
