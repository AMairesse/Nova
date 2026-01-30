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
- Prompt:
  - do not inject memory content
  - only inject short instructions to use `memory.search` / `memory.get` / `memory.add`
- UI:
  - new “Memory settings” is repurposed to configure embeddings provider + includes a “Test embeddings endpoint” healthcheck
  - configuration stored at user-level (UserParameters)

Design spec: [`plans/memory.md`](plans/memory.md)
