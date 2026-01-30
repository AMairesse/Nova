# Current Context

## Current Work Focus

**In progress: redesign long-term memory**

- Goal: replace current Markdown/theme-based memory with structured memory items.
- Retrieval: hybrid search (PostgreSQL FTS + pgvector embeddings).
- Embeddings: computed asynchronously via Celery with FTS fallback.
- Provider: configurable HTTP endpoint; auto-prefer `llama.cpp` as a system provider when available.
- Prompt: avoid injecting memory content; rely on tool calls (`memory.search`).

Design spec draft: [`plans/memory.md`](plans/memory.md)
