# Embeddings (Memory + Conversation)

Last reviewed: 2026-03-31
Status: implemented

## Scope

Embeddings are optional and currently power semantic retrieval for:
- long-term memory
- continuous conversation recall (day summaries + transcript chunks)

All vector columns are fixed to 1024 dimensions.

## Data model

Memory:
- `MemoryItemEmbedding` (OneToOne with `MemoryItem`)

Conversation:
- `DaySegmentEmbedding` (OneToOne with `DaySegment`)
- `TranscriptChunkEmbedding` (OneToOne with `TranscriptChunk`)

Embedding state lifecycle:
- `pending`
- `ready`
- `error`

User configuration:
- `UserParameters.memory_embeddings_source`
  - `system`
  - `custom`
  - `disabled`
- Custom endpoint values stay persisted on `UserParameters` even when the active
  source is `system` or `disabled`.

## Provider resolution

Runtime code uses one shared provider resolution contract for sync and async paths:

1. `custom` source:
- use the per-user DB config from `UserParameters`
- no fallback to `system`

2. `system` source:
- use the deployment-level `MEMORY_EMBEDDINGS_*` settings
- no fallback to `custom`

3. `disabled` source:
- no provider

`LLAMA_CPP_*` is not part of embeddings resolution.

If no provider is available, semantic computation is skipped and lexical search remains active.

## System provider backfill

The deployment-level embeddings provider is tracked through a singleton DB row:
- availability
- current fingerprint
- last successful backfill fingerprint/state

When the system provider appears or changes fingerprint, Nova lazily schedules:
- `rebuild_user_memory_embeddings_task`
- `rebuild_user_conversation_embeddings_task`

This auto-backfill is limited to users still configured with `memory_embeddings_source="system"`.

## Vector generation contract

`compute_embedding(...)` expects an OpenAI-like `/embeddings` payload.

Dimension policy:
- vectors shorter than 1024 are zero-padded
- vectors longer than 1024 raise an error

## Background tasks

Memory tasks:
- `compute_memory_item_embedding_task`
- `rebuild_user_memory_embeddings_task`

Conversation tasks:
- `compute_day_segment_embedding_task`
- `compute_transcript_chunk_embedding_task`
- `rebuild_user_conversation_embeddings_task`

Rebuild tasks mark vectors as pending and requeue recomputation in batches.

## Retrieval usage

Memory search:
- PostgreSQL: hybrid (FTS + semantic) when query vector exists
- fallback: lexical only

Conversation search:
- PostgreSQL: hybrid across day summaries and transcript chunks when vectors are ready
- fallback: lexical only

## Operational notes

- Embeddings are optional; feature behavior degrades gracefully to lexical retrieval.
- The memory settings screen lets users choose `system`, `custom`, or `disabled`.
- Provider changes from the memory settings screen trigger rebuild confirmation only
  when the effective provider changes from one concrete provider to another.
