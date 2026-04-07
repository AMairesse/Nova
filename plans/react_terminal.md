# React Terminal

Last reviewed: 2026-04-06  
Status: implemented

## Goal

Nova uses a single terminal-first runtime centered on:

- `terminal(command: str)`
- `delegate_to_agent(agent_id: str, question: str, input_paths: list[str] | null)`
- `ask_user(question: str, schema?: object)`

The runtime is file-centric, plugin-backed, and designed around a persistent pseudo-terminal mental model.

## Runtime Package

Core package:

- `nova/runtime/`

Main responsibilities:

- provider-aware chat/tool orchestration
- VFS projection and shell execution
- runtime compaction/session state
- task execution and resume handling
- sub-agent delegation

## Internal Plugins

Builtin and system capabilities are described through the internal plugin registry in:

- `nova/plugins/`

Each plugin can define:

- builtin subtype mapping
- settings metadata
- runtime capability resolution
- skill docs exposed under `/skills`
- optional connection test behavior

Current plugin families include:

- `terminal`
- `history`
- `datetime`
- `memory`
- `mail`
- `calendar`
- `search`
- `browser`
- `downloads`
- `webdav`
- `webapp`
- `python`
- `mcp`
- `api`

## Filesystem Model

### Persistent root

- `/` is the main persistent working area for the thread
- visible files are stored as `UserFile(scope=THREAD_SHARED)`

### Virtual mounts

- `/skills`: readonly runtime docs
- `/memory`: user-scoped Markdown memory workspace
- `/webdav`: remote WebDAV mounts when configured
- `/tmp`: hidden scratch area backed by `UserFile(scope=MESSAGE_ATTACHMENT)`
- `/subagents/<agent-id>-<run-id>/`: copied-back outputs from delegated runs

## Memory Model

`/memory` is backed by:

- `MemoryDirectory`
- `MemoryDocument`
- `MemoryChunk`
- `MemoryChunkEmbedding`

Characteristics:

- user-scoped, shared across that user’s runs
- Markdown-only documents
- free path structure under `/memory`
- chunk-based semantic retrieval

Runtime surface:

- normal file commands (`ls`, `cat`, `mkdir`, `touch`, `tee`, `mv`, `rm`)
- lexical `grep`
- hybrid `memory search`

## Continuous Mode

Continuous threads use:

- stored messages
- `DaySegment`
- persisted day summaries
- `TranscriptChunk`
- optional embeddings

Runtime commands:

- `history search`
- `history get`

## Integration Model

### Web

- `search`
- `browse ...`
- `curl`
- `wget`

### Mail

- `mail accounts`
- `mail list`
- `mail read`
- `mail send`
- related mailbox/file subcommands

### Calendar

- `calendar accounts`
- `calendar calendars`
- `calendar upcoming`
- `calendar list`
- `calendar search`
- `calendar show`
- `calendar create`
- `calendar update`
- `calendar delete`

### WebDAV

WebDAV is exposed as mounted directories under `/webdav/<mount-name>` and reuses normal filesystem commands.

### Web Apps

Web apps are live publications from normal workspace files:

- create/edit files under `/`
- publish with `webapp expose <source_dir>`
- serve under `/apps/<slug>/`

### MCP

MCP servers are attached as tools with `tool_type=mcp` and surfaced through:

- `mcp servers`
- `mcp tools`
- `mcp schema`
- `mcp call`
- `mcp refresh`

MCP managed OAuth is configured in settings and refreshed silently at runtime when possible.

### Custom API

Custom API services are attached as tools with `tool_type=api` and surfaced through:

- `api services`
- `api operations`
- `api schema`
- `api call`

Each service owns declared `APIToolOperation` rows.

## Sub-Agents

Delegation remains explicit through `delegate_to_agent(...)`.

Behavior:

- child runs get an isolated filesystem
- selected parent files are copied into `/inbox/...`
- child persistent outputs are copied back under `/subagents/...`
- child scratch files are not copied back
- memory can be shared when both parent and child have memory capability

## Ask User

`ask_user(...)` is native to the main runtime:

- creates an `Interaction`
- pauses the task with `AWAITING_INPUT`
- resumes through the runtime-aware resume path

It is not exposed to sub-agents.

## Storage Summary

- visible workspace files -> `UserFile(scope=THREAD_SHARED)`
- scratch/runtime hidden files -> `UserFile(scope=MESSAGE_ATTACHMENT)`
- memory -> `MemoryDirectory`, `MemoryDocument`, `MemoryChunk`, `MemoryChunkEmbedding`
- live webapps -> `WebApp` metadata + normal `UserFile` source files
