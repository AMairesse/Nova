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
  - `superseded`
  - `archived`
- `supersedes` (FK → self, nullable) (optional link to indicate replacement)
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

- Always compute lexical match (FTS).
- If embeddings provider configured:
  - compute query embedding (in-process or via cached provider client)
  - compute semantic similarity for items with `state=ready`
  - combine rankings (hybrid)
- If provider configured but no embeddings ready yet:
  - return FTS-only results

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
- If embeddings are enabled:
  - create/update `MemoryItemEmbedding(state=pending)`
  - enqueue Celery task to compute the embedding.

#### 3.2.3 `memory.get()`

Input: `item_id`

Output: full item fields + embedding state.

#### 3.2.4 `memory.update()` / `memory.delete()`

These are optional for v1; they can exist but can also be intentionally omitted to keep the surface small.

#### 3.2.5 `memory.list_themes()`

Returns list of `(slug, display_name)` for this user.

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

### 3.3 Prompt injection contract

Goal: **do not inject memory content** by default.

Proposed injected block when memory tool is enabled:

- 1–2 lines instructing the agent to use `memory.search` for user-specific context.
- Optionally list up to N themes (N small, e.g. 10) so the agent knows what exists.

Hard rule:

- never inject the full memory store

Rationale:

- memory is accessed through tools so prompt stays bounded
- avoids needing opportunistic consolidation in thread mode

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
