# React Terminal V2

## Goal

Build the experimental Nova runtime around a persistent pseudo-terminal with a
very small tool surface and a file-centric mental model.

## Locked Decisions

- Runtime selection is per-agent during the experimentation phase.
- No legacy <-> v2 interoperability is required.
- V2 supports standard threads and continuous threads.
- V2 targets OpenAI-compatible providers only.
- V2 exposes a stable model tool surface:
  - `terminal(command: str)`
  - `delegate_to_agent(agent_id: str, question: str, input_paths: list[str] | null)`
- No `ask_user`, `load_skill`, or `list_skills`.
- Skills are documentation only, exposed as virtual markdown files under `/skills`.
- V2 is file-centric and does not rely on `MessageArtifact` for normal runtime flows.
- Long-term memory is canonical in the database and exposed as a user-scoped
  virtual mount under `/memory` only when the agent has memory capability.
- Continuous threads do not use v2 compaction; they rely on day summaries plus
  `history search` / `history get`.

## Current Target Architecture

### Runtime

- Direct ReAct loop implemented in `nova/runtime_v2/`
- No LangChain, LangGraph, or Langfuse in the v2 runtime package
- Reuses the existing realtime/frontend contract for streaming, progress, trace
  footer data, and compaction UI
- Standard threads load raw thread history, optionally preceded by v2 compaction
  state stored in `AgentThreadSession`
- Continuous threads load context through the continuous context builder:
  previous day summaries plus the current-day raw window
- React Terminal compaction is explicitly disabled in continuous mode

### Filesystem model

- `/`: persistent files for the agent/thread and the main visible working area
- `/skills`: virtual readonly recipes
- `/memory`: virtual user-scoped durable memory shared across v2 agents that have
  memory capability
- `/webdav`: terminal-only remote WebDAV mounts when the agent has WebDAV capability
- `/tmp`: scratch files visible in the terminal but hidden from the normal file UI
- `/subagents/<agent-id>-<run-id>/`: outputs copied back automatically from delegated sub-agents
- Live webapp source directories live in the normal persistent root under `/`
  and are published explicitly with `webapp expose`
- MCP and custom API integrations are terminal-native command families, not filesystems

### Storage mapping

- Visible persistent root paths (`/foo.txt`, `/docs/report.md`, `/subagents/...`) are stored as:
  - `UserFile(scope=THREAD_SHARED)`
- `/memory/...` is not stored in MinIO; it is projected from:
  - `MemoryDirectory`
  - `MemoryDocument`
  - `MemoryChunk`
  - `MemoryChunkEmbedding`
- `/webdav/...` is not stored in MinIO; it is projected live from the configured
  WebDAV tools and remains hidden from the normal thread file UI
- `/tmp/...` is stored in MinIO too, but as:
  - `UserFile(scope=MESSAGE_ATTACHMENT)`
  - under a hidden runtime prefix
- Existing hidden v2 workspace files are still remapped into `/` for migration compatibility

### Memory model

- `/memory` is user-scoped and shared across all v2 threads for the same user
- `/memory` is also shared with sub-agents that have memory capability
- Supported visible paths:
  - `/memory/README.md`
  - `/memory/<file>.md`
  - `/memory/<dir>/<file>.md`
  - arbitrary nested directories created explicitly with `mkdir`
- There is no imposed type or theme hierarchy in `/memory`
- Memory files are plain Markdown documents without YAML frontmatter
- File writes support creation and editing through terminal-native commands such as
  `touch`, `tee`, `mv`, and `rm`
- `rm /memory/<file>.md` archives the underlying memory document instead of deleting a MinIO object
- `rm /memory/<dir>` is allowed only when the directory is empty
- Markdown documents are chunked for retrieval:
  - split by `##` sections first
  - oversized sections are re-chunked into overlapping text windows
- Embeddings are stored per chunk, never per whole file
- Memory writes always create the chunk and `MemoryChunkEmbedding` records immediately
- If an embeddings provider is available at write time, chunk computation is queued immediately
- If no provider is available, or if queueing fails, embeddings stay `pending` and are picked up later by the rebuild flow
- `grep` is lexical only
- `memory search ...` is the semantic/hybrid retrieval command and returns file path plus matching section
- Legacy callable memory tools are no longer part of the runtime surface

### WebDAV model

- `/webdav` is mounted only when at least one WebDAV builtin is configured on the agent
- Each configured WebDAV tool appears under:
  - `/webdav/<mount-name>`
- Mount names are derived from the tool name, with `-<tool_id>` suffixes only on collisions
- The WebDAV tool `root_path` becomes the visible root of the mount
- The v2 runtime never lets the agent escape above that configured root
- Reads and writes reuse the normal terminal filesystem commands rather than a separate
  `webdav ...` command family
- Recursive `find`/`grep -r` traversals over WebDAV are capped at 500 remote paths per command
- Cross-boundary directory copy/move between local storage and WebDAV is intentionally unsupported in v1

### Web access model

- Direct HTTP(S) downloads stay terminal-native through:
  - `curl`
  - `wget`
- Web search is exposed through:
  - `search <query> [--limit N] [--output /path.json]`
- Interactive page reading is exposed through:
  - `browse open`
  - `browse current`
  - `browse back`
  - `browse text`
  - `browse links`
  - `browse elements`
  - `browse click`
- Playwright browser state is ephemeral and exists only for the current run
- Cached `search` results also exist only for the current run and can be reopened with:
  - `browse open --result N`
- Persisted outputs from `search` or `browse` must be written explicitly through `--output`

### MCP model

- MCP servers are configured as attached agent tools with `tool_type=mcp`
- MCP is exposed terminal-natively through:
  - `mcp servers`
  - `mcp tools`
  - `mcp schema`
  - `mcp call`
  - `mcp refresh`
- MCP tools are discovered live from the configured server rather than loaded as model tools
- Complex inputs can be provided inline, via `--input-file`, or through stdin JSON
- When `mcp` output is piped or redirected, the command emits normalized JSON to stdout
- `mcp call --output /path.json` writes the normalized payload to the VFS
- `mcp call --extract-to /dir` materializes extractable files/resources returned by the MCP result
- Binary/resource-like MCP results are not piped as raw binary in v1

### Custom API model

- Custom API services are configured as attached agent tools with `tool_type=api`
- Each API service owns declared operations modeled as `APIToolOperation`
- An operation defines:
  - HTTP method
  - path template
  - query parameter names
  - optional body parameter
  - input and output schemas
- API is exposed terminal-natively through:
  - `api services`
  - `api operations`
  - `api schema`
  - `api call`
- Complex inputs can be provided inline, via `--input-file`, or through stdin JSON
- When `api` output is piped or redirected, the command emits normalized JSON to stdout
- Binary API responses must be saved explicitly with `--output`
- Supported auth modes in v1:
  - none
  - basic
  - bearer/token
  - api_key in header or query

### WebApp model

- Webapps are thread-scoped publications backed by a live source directory in the
  normal persistent VFS
- A published webapp stores:
  - `source_root`
  - `entry_path`
- There is no snapshot table and no separate published-file storage
- The canonical source files are normal `UserFile(scope=THREAD_SHARED)` entries
  under the published source directory
- Published source directories must not live under:
  - `/skills`
  - `/tmp`
  - `/memory`
  - `/webdav`
- `/apps/<slug>/` serves the configured `entry_path`
- `/apps/<slug>/<path>` serves the matching live file under `source_root`
- Publishing is live:
  - `webapp expose` creates or updates the publication
  - later filesystem mutations to the source directory are reflected directly
  - terminal-side mutations trigger the existing `webapp_update` and `webapps_update`
    realtime events automatically
- If the published entry file disappears, the webapp becomes broken until the
  source directory is fixed or re-exposed

### Sub-agents

- Delegation stays on the dedicated `delegate_to_agent(...)` tool
- Sub-agents are isolated from the parent filesystem
- Parent input files are copied into the child under `/inbox/...`
- Child agents with memory capability see the same `/memory` mount as the parent
- Files created or modified by the child persistent root are copied back automatically into the parent under:
  - `/subagents/<agent-id>-<run-id>/...`
- Child `/tmp` files are never copied back

### Capabilities

- Base shell-like commands:
  - `pwd`, `ls`, `cd`, `cat`, `head`, `tail`, `mkdir`, `touch`, `tee`, `cp`, `mv`, `rm`, `find`
- Calendar command family:
  - `calendar accounts`, `calendar calendars`, `calendar upcoming`, `calendar list`, `calendar search`, `calendar show`, `calendar create`, `calendar update`, `calendar delete`
- Continuous mode command family:
  - `history search ...`
  - `history get ...`
- Memory command family:
  - `grep ...`
  - `memory search ...`
- Optional command families enabled by configured tools:
  - API tool -> `api ...`
  - caldav builtin -> `calendar ...`
  - browser builtin -> `curl`, `wget`, `browse ...`
  - email builtin -> `mail ...`
  - code execution builtin -> `python ...`
  - date builtin -> `date`
  - memory builtin -> `/memory` mount + `memory search`
  - MCP tool -> `mcp ...`
  - searxng builtin -> `search ...`
  - webapp builtin -> `webapp list`, `webapp expose`, `webapp show`, `webapp delete`
  - webdav builtin -> `/webdav` mount through existing filesystem commands

## Implemented

- Runtime selection by agent with `react_terminal_v1`
- Persistent terminal session state stored in `AgentThreadSession`
- OpenAI-compatible v2 provider client
- Streaming, progress updates, reconnect support, context footer data, and trace footer wiring
- Compaction stored in `AgentThreadSession.session_state`
- Thread title generation reused from the existing pipeline
- Virtual skills registry
- Mailbox-aware mail commands, including multi-mailbox selection through `--mailbox`
- Native `date` command
- `python` execution with optional `--output`
- Root-oriented VFS implementation with directory-aware copy/move/output resolution
- Hidden `/tmp` stored in MinIO
- Isolated sub-agent runtime roots with automatic output copy-back into `/subagents/...`
- Continuous mode support in the v2 runtime
- Continuous recall through terminal-native `history search` and `history get`
- Continuous-specific virtual skill documentation under `/skills/continuous.md`
- Shared database-backed `/memory` mount with terminal read/write support
- Free-form Markdown memory files under `/memory` with no imposed type/theme hierarchy
- Free-form memory documents and persistent empty memory directories
- Chunk-based memory indexing and embeddings
- Immediate memory-embedding scheduling when available, with pending fallback and surfaced warnings when queueing fails
- Terminal-native `grep` for lexical search across real and virtual text files
- Terminal-native `memory search` for hybrid lexical + embeddings retrieval
- Legacy callable memory tools removed in favor of `/memory` + terminal commands
- Shared WebDAV service used by both the legacy WebDAV builtin and the v2 runtime
- Terminal-only `/webdav` mount with per-tool mounts derived from configured WebDAV builtins
- WebDAV reads/writes/moves/copies through the existing filesystem commands while honoring the legacy `allow_*` flags
- Reserved `/webdav` paths when WebDAV capability is absent
- Recursive WebDAV traversal guardrail at 500 examined remote paths per command
- Shared HTTP download service used by the legacy browser builtin and by v2 `curl` / `wget`
- Shared SearXNG service used by the legacy SearXNG builtin and by v2 `search`
- Shared CalDAV service used by both the legacy CalDAV builtin and the v2 runtime
- Terminal-native `calendar` command family with multi-account selection through `--account`
- JSON/Markdown export support for calendar read commands through `--output`
- Recurring CalDAV events exposed in read flows but treated as read-only for create/update/delete
- API tools modeled as declared `APIToolOperation` records under a configured API service
- Terminal-native `api` command family with stdin, pipes, shell redirections, and explicit `--output` for binary responses
- Terminal-native `mcp` command family with live tool discovery, schema inspection, input via files/stdin, and optional extraction through `--extract-to`
- MCP tools removed from the legacy model-tool loading path; MCP and API are now terminal-only capabilities in v2
- Terminal-native `search` command with per-run cached results and optional JSON persistence
- Terminal-native `browse` command family backed by a native Playwright service
- Lazy Playwright session creation and guaranteed browser cleanup at the end of each v2 run
- Browser-specific skill docs under `/skills/search.md` and `/skills/browse.md`
- Live webapp publishing from terminal-authored source directories through
  `webapp expose`
- Thread-scoped `WebApp` records storing `source_root` and `entry_path`
- Live webapp serving directly from `UserFile(scope=THREAD_SHARED)` instead of
  `WebAppFile`
- Automatic live-webapp refresh propagation for terminal filesystem mutations
- Legacy callable webapp tools removed in favor of the terminal-native `webapp`
  command family
- Legacy `WebAppFile` storage removed without content migration
- Webapp skill docs under `/skills/webapp.md`

## Next Steps

- Run the broader full-suite validation after the memory migration lands cleanly in real data
- Decide when to stop exposing the old legacy memory models/admin screens entirely, now that the canonical runtime path is document-based
- Add broader runtime coverage around memory path collisions and more cross-agent memory scenarios beyond the current focused tests
- Add or harden delegation-focused tests if edge cases remain around nested directories or modified input files
- Decide which remaining legacy-only capabilities deserve a terminal-native v2 mapping next
- Evaluate whether browser form entry, richer interactions, or screenshots are worth adding beyond the current targeted-reading scope
- Sweep remaining product/UI text for any stale `/thread` or `/workspace` wording outside the v2 runtime package
- Consider whether the file sidebar should eventually surface `/subagents/...` differently from other root files
- Evaluate whether webapps eventually need a build step or SPA routing fallback beyond the current static live-serving model
- Evaluate whether more terminal-native commands are worth adding without bloating the command language

## Out of Scope for V1

- Non OpenAI-compatible providers
- Legacy interoperability
- `MessageArtifact`-centric workflows in v2
- Full bash emulation
- Pipes, redirections, globbing, heredocs, shell chaining, and shell substitutions
- Interactive editors
- Shared writable filesystem between parent and sub-agents
- Binary storage inside `/memory`
- Webapp build steps, package managers, or bundler pipelines
- SPA history-api fallback routing for webapps
