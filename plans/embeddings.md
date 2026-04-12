# Embeddings

Last reviewed: 2026-04-06  
Status: implemented

## Scope

Embeddings are optional and currently power semantic retrieval for:

- long-term memory
- continuous conversation recall

All vector columns use 1024 dimensions.

## Data Model

### Memory

- `MemoryChunkEmbedding` (OneToOne with `MemoryChunk`)

### Continuous conversation

- `DaySegmentEmbedding` (OneToOne with `DaySegment`)
- `TranscriptChunkEmbedding` (OneToOne with `TranscriptChunk`)

Embedding state lifecycle:

- `pending`
- `ready`
- `error`

## User Configuration

`UserParameters.memory_embeddings_source` controls embeddings resolution:

- `system`
- `custom`
- `disabled`

If no provider is available, Nova keeps lexical retrieval active and skips semantic computation.

## Memory-Specific Behavior

Memory embeddings are chunk-based:

- a memory document is split into `MemoryChunk` rows
- `MemoryChunkEmbedding` rows are created at write time
- if a provider is available, computation is queued immediately
- if no provider is available, rows stay `pending` for later rebuild

This keeps memory writes independent from broker/provider availability.

## Continuous-Specific Behavior

Continuous retrieval uses embeddings on:

- day summaries
- transcript chunks

Search remains hybrid on PostgreSQL and lexical-only on simpler backends or when vectors are unavailable.

## Provider Resolution

Resolution contract:

1. `custom`
   - use per-user DB config only
2. `system`
   - use deployment-level embeddings settings only
3. `disabled`
   - no provider

## Background Tasks

Memory:

- rebuild user memory embeddings
- compute chunk embeddings

Continuous:

- rebuild user conversation embeddings
- compute day-segment embeddings
- compute transcript-chunk embeddings

## Dimension Policy

- vectors shorter than 1024 are zero-padded
- vectors longer than 1024 raise an error
