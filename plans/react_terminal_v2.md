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

### Storage mapping

- Visible persistent root paths (`/foo.txt`, `/docs/report.md`, `/subagents/...`) are stored as:
  - `UserFile(scope=THREAD_SHARED)`
- `/memory/...` is not stored in MinIO; it is projected from:
  - `MemoryTheme`
  - `MemoryItem`
  - `MemoryItemEmbedding`
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
  - `/memory/<theme>/<file>.md`
  - `/memory/<theme>/<file>.txt`
- Theme is determined by the directory name, not by file frontmatter
- File reads project memory items as YAML frontmatter plus body content
- File writes support creation and editing through terminal-native commands such as
  `touch`, `tee`, `mv`, and `rm`
- `rm /memory/...` archives the memory item instead of deleting a MinIO object
- `grep` is lexical only
- `memory search ...` is the semantic/hybrid retrieval command

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
- Continuous mode command family:
  - `history search ...`
  - `history get ...`
- Memory command family:
  - `grep ...`
  - `memory search ...`
- Optional command families enabled by configured tools:
  - browser builtin -> `curl`, `wget`
  - email builtin -> `mail ...`
  - code execution builtin -> `python ...`
  - date builtin -> `date`
  - memory builtin -> `/memory` mount + `memory search`
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
- Thin shared memory service used by both the legacy memory builtin and the v2 runtime
- Terminal-native `grep` for lexical search across real and virtual text files
- Terminal-native `memory search` for hybrid lexical + embeddings retrieval
- Shared WebDAV service used by both the legacy WebDAV builtin and the v2 runtime
- Terminal-only `/webdav` mount with per-tool mounts derived from configured WebDAV builtins
- WebDAV reads/writes/moves/copies through the existing filesystem commands while honoring the legacy `allow_*` flags
- Reserved `/webdav` paths when WebDAV capability is absent
- Recursive WebDAV traversal guardrail at 500 examined remote paths per command

## Next Steps

- Add broader runtime coverage around memory path collisions, archived-item retrieval,
  and cross-agent memory scenarios beyond the current focused tests
- Add or harden delegation-focused tests if edge cases remain around nested directories or modified input files
- Decide which remaining legacy-only capabilities deserve a terminal-native v2 mapping next
- Sweep remaining product/UI text for any stale `/thread` or `/workspace` wording outside the v2 runtime package
- Consider whether the file sidebar should eventually surface `/subagents/...` differently from other root files
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
