# React Terminal V2

## Goal

Build the experimental Nova runtime around a persistent pseudo-terminal with a
very small tool surface and a file-centric mental model.

## Locked Decisions

- Runtime selection is per-agent during the experimentation phase.
- No legacy <-> v2 interoperability is required.
- V2 targets standard thread mode only.
- V2 targets OpenAI-compatible providers only.
- V2 exposes a stable model tool surface:
  - `terminal(command: str)`
  - `delegate_to_agent(agent_id: str, question: str, input_paths: list[str] | null)`
- No `ask_user`, `load_skill`, or `list_skills`.
- Skills are documentation only, exposed as virtual markdown files under `/skills`.
- V2 is file-centric and does not rely on `MessageArtifact` for normal runtime flows.

## Current Target Architecture

### Runtime

- Direct ReAct loop implemented in `nova/runtime_v2/`
- No LangChain, LangGraph, or Langfuse in the v2 runtime package
- Reuses the existing realtime/frontend contract for streaming, progress, trace
  footer data, and compaction UI

### Filesystem model

- `/`: persistent files for the agent/thread and the main visible working area
- `/skills`: virtual readonly recipes
- `/tmp`: scratch files visible in the terminal but hidden from the normal file UI
- `/subagents/<agent-id>-<run-id>/`: outputs copied back automatically from delegated sub-agents

### Storage mapping

- Visible persistent root paths (`/foo.txt`, `/docs/report.md`, `/subagents/...`) are stored as:
  - `UserFile(scope=THREAD_SHARED)`
- `/tmp/...` is stored in MinIO too, but as:
  - `UserFile(scope=MESSAGE_ATTACHMENT)`
  - under a hidden runtime prefix
- Existing hidden v2 workspace files are still remapped into `/` for migration compatibility

### Sub-agents

- Delegation stays on the dedicated `delegate_to_agent(...)` tool
- Sub-agents are isolated from the parent filesystem
- Parent input files are copied into the child under `/inbox/...`
- Files created or modified by the child persistent root are copied back automatically into the parent under:
  - `/subagents/<agent-id>-<run-id>/...`
- Child `/tmp` files are never copied back

### Capabilities

- Base shell-like commands:
  - `pwd`, `ls`, `cd`, `cat`, `head`, `tail`, `mkdir`, `touch`, `tee`, `cp`, `mv`, `rm`, `find`
- Optional command families enabled by configured tools:
  - browser builtin -> `curl`, `wget`
  - email builtin -> `mail ...`
  - code execution builtin -> `python ...`
  - date builtin -> `date`

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

## Next Steps

- Run the updated targeted test suite and fix any regressions uncovered by the VFS layout change
- Add or harden delegation-focused tests if edge cases remain around nested directories or modified input files
- Sweep remaining product/UI text for any stale `/thread` or `/workspace` wording outside the v2 runtime package
- Consider whether the file sidebar should eventually surface `/subagents/...` differently from other root files
- Evaluate whether more terminal-native commands are worth adding without bloating the command language

## Out of Scope for V1

- Continuous mode
- Non OpenAI-compatible providers
- Legacy interoperability
- `MessageArtifact`-centric workflows in v2
- Full bash emulation
- Pipes, redirections, globbing, heredocs, shell chaining, and shell substitutions
- Interactive editors
- Shared writable filesystem between parent and sub-agents
