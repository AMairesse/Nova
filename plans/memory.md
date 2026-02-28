# Long-term Memory (Implemented)

Last reviewed: 2026-02-28
Status: implemented

## Scope

Nova memory is a structured, user-scoped store accessed through tools (not injected as full prompt content).

## Data model

Implemented models:
- `MemoryTheme`
- `MemoryItem`
- `MemoryItemEmbedding`

Key points:
- Memory is global per user.
- `MemoryItem.status` supports `active` and `archived`.
- Embeddings use fixed-size pgvector storage (`VectorField(dimensions=1024)`).
- PostgreSQL-specific vector index migration is present for embeddings.

## Tool surface

Builtin memory tool functions:
- `memory_search`
- `memory_add`
- `memory_get`
- `memory_list_themes`
- `memory_archive`

Behavior highlights:
- `memory_add` creates structured items and defaults missing theme to `general`.
- `memory_search` defaults to `status=active`.
- `memory_search` accepts match-all mode (`query=''` or `query='*'`) and returns recent items.
- `memory_archive` performs soft-delete (`status=archived`).

## Retrieval behavior

`memory_search` uses DB-dependent retrieval:
- PostgreSQL: hybrid ranking (FTS + semantic when query vector is available).
- Without embeddings/query vector: lexical ranking only.
- Non-PostgreSQL fallback: `icontains`.

Returned payload includes score/signals metadata and snippets.

## Prompt contract

Memory is tool-driven in prompt generation:
- Full memory content is not injected.
- Prompt includes lightweight discovery hints (themes + active counts) when memory tool is enabled.

## Embeddings operations

Implemented background tasks:
- `compute_memory_item_embedding_task`
- `rebuild_user_memory_embeddings_task`

Operational behavior:
- Embedding computation is asynchronous.
- If no embeddings provider is configured, memory still works in lexical mode.
- Rebuild task marks vectors pending and requeues computation in batches.

Dimension handling:
- vectors shorter than 1024 are zero-padded.
- vectors longer than 1024 raise an error.

## User settings and inspection UI

Implemented user-facing memory settings in `user_settings`:
- per-user embeddings configuration fields (stored in `UserParameters`)
- endpoint healthcheck action
- confirmation flow before provider/model change when rebuild is needed
- background rebuild trigger after confirmation

Implemented read-only browser:
- paginated memory item table
- optional archived inclusion toggle
- simple theme/text filtering

## Out of scope in this document

This file does not describe deprecated markdown-blob memory or speculative future consolidation designs.
