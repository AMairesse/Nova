# Long-Term Memory

Last reviewed: 2026-04-06  
Status: implemented

## Scope

Nova memory is a user-scoped Markdown workspace exposed through the runtime as `/memory`.

It is not stored as prompt text and is modeled as documents, directories, and chunks.

## Data Model

Implemented models:

- `MemoryDirectory`
- `MemoryDocument`
- `MemoryChunk`
- `MemoryChunkEmbedding`

Key points:

- memory is global per user
- `MemoryDocument.virtual_path` is the source of truth for visible file paths
- empty directories are persisted through `MemoryDirectory`
- documents are soft-archived through status fields

## Visible Runtime Surface

Examples:

- `/memory/README.md`
- `/memory/profile.md`
- `/memory/projects/client-a.md`

Supported operations come from normal terminal commands:

- `ls`
- `cat`
- `mkdir`
- `touch`
- `tee`
- `mv`
- `rm`
- `grep`
- `memory search`

## Retrieval Behavior

Lexical:

- `grep` works on rendered document text only

Hybrid semantic:

- `memory search` runs on `MemoryChunk`
- supports `--under /memory/...`
- returns path + matching heading/section

## Chunking

Documents are chunked with a Markdown-first strategy:

- split by `##` sections first
- oversized sections fall back to overlapping windows
- documents without headings are window-chunked directly

## Embeddings

- embeddings are stored per chunk, never per whole file
- `MemoryChunkEmbedding` rows are created immediately on write
- async computation is queued when a provider is available
- pending rows are later picked up by rebuild flows when needed

## UI / Settings

User-facing memory settings include:

- embeddings source and provider config
- rebuild confirmation when the effective provider changes
- a document-centric browser

The browser lists documents/directories rather than typed memory items.
