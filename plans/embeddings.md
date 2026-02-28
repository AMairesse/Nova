# Embeddings (Memory + Conversation)

Last reviewed: 2026-02-28
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

## Provider resolution

Runtime code uses two provider resolution paths:

1. Async path (`aget_embeddings_provider`, used by query embedding/tool paths):
- system `LLAMA_CPP_SERVER_URL` + `LLAMA_CPP_MODEL`
- then per-user DB config (`UserParameters`)
- then legacy env fallback (`MEMORY_EMBEDDINGS_URL`)

2. Sync path (`get_embeddings_provider`, used by Celery embedding tasks):
- env `MEMORY_EMBEDDINGS_URL`
- then per-user DB config (when `user_id` is provided)

If no provider is available, semantic computation is skipped and lexical search remains active.

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
- Provider/model changes from user settings trigger background memory re-embedding confirmation/rebuild flow.
- Conversation embeddings are refreshed by transcript/day-summary pipelines and dedicated rebuild task.
