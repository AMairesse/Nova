# React Terminal V2

## Goal

Build a new experimental agent runtime that is independent from the legacy
LangChain/LangGraph stack and centered around a persistent pseudo-terminal.

## Locked Decisions

- Runtime selection is per-agent.
- The legacy runtime remains available only for comparison/testing.
- No legacy <-> v2 interoperability is required.
- V2 targets standard thread mode only.
- V2 targets OpenAI-compatible providers only.
- V2 exposes a minimal stable tool surface:
  - `terminal(command: str)`
  - `delegate_to_agent(agent_id: str, question: str, input_paths: list[str] | null)`
- No `ask_user`, `load_skill`, or `list_skills`.
- No artifact-centric workflow in v2. The agent only manipulates files through a
  virtual file system.
- Skills are exposed as virtual markdown files under `/skills`.

## Architecture

### Runtime

- New runtime package under `nova/runtime_v2/`
- No LangChain/LangGraph/Langfuse imports in the new package
- ReAct loop implemented directly against an OpenAI-compatible client

### VFS

- `/skills`: readonly virtual markdown files
- `/thread`: durable thread files backed by `UserFile(scope=THREAD_SHARED)`
- `/workspace`: runtime working files backed by `UserFile`, without
  `MessageArtifact`
- `/tmp`: optional alias for `/workspace`

### Capabilities

- Base shell-like commands always enabled
- Extra command families depend on configured tools:
  - email builtin -> `mail *`
  - browser builtin -> `curl`, `wget`
  - code_execution builtin -> `python ...`
- Sub-agent delegation remains a dedicated tool, not a terminal command

## Phases

### Phase 1: Runtime skeleton

- [x] Add `runtime_engine` to `AgentConfig`
- [x] Add `AgentThreadSession` model
- [x] Create `nova/runtime_v2/` package
- [x] Implement OpenAI-compatible provider client
- [x] Implement stable v2 system prompt builder
- [x] Implement v2 ReAct loop

### Phase 2: Terminal and VFS

- [x] Implement persistent terminal session state
- [x] Implement VFS path resolution
- [x] Implement `/skills` virtual registry
- [x] Implement base commands:
  - [x] `pwd`
  - [x] `ls`
  - [x] `cd`
  - [x] `cat`
  - [x] `head`
  - [x] `tail`
  - [x] `mkdir`
  - [x] `cp`
  - [x] `mv`
  - [x] `rm`
  - [x] `find`

### Phase 3: Capability-backed commands

- [x] Implement `curl`
- [x] Implement `wget`
- [x] Implement `mail list`
- [x] Implement `mail read`
- [x] Implement `mail attachments`
- [x] Implement `mail import`
- [x] Implement `mail send`
- [x] Implement `python <script.py>`
- [x] Implement `python -c "..."`

### Phase 4: Product integration

- [x] Route thread execution to v2 when selected on the agent
- [x] Restrict v2 to thread mode
- [x] Add runtime selection to agent settings UI/form
- [x] Merge message attachments into thread files for v2 submissions
- [x] Reject unsupported providers cleanly for v2

### Phase 5: Sub-agents

- [x] Implement `delegate_to_agent(...)`
- [x] Restrict delegation to v2 sub-agents only
- [x] Copy sub-agent output files back into parent workspace

### Phase 6: Tests

- [x] Add model/runtime selection tests
- [x] Add terminal parser and VFS tests
- [x] Add v2 executor tests with mocked provider responses
- [x] Add thread submission tests for v2 file handling
- [ ] Add delegation tests

### Phase 7: Realtime UI parity

- [x] Reuse the existing websocket/frontend contract for v2
- [x] Stream assistant output progressively through `TaskProgressHandler`
- [x] Persist `Task.current_response` and `Task.streamed_markdown` during v2 runs
- [x] Publish progress updates for generation, tool execution, and finalization
- [x] Persist final agent message footer metadata:
  - [x] context consumption
  - [x] execution trace link data
  - [x] compact button visibility compatibility

### Phase 8: V2 compaction

- [x] Enable compaction route for React Terminal V1 agents
- [x] Store compaction state in `AgentThreadSession.session_state`
- [x] Inject the compacted summary back into v2 history loading
- [x] Reuse the existing `summarization_complete` websocket event
- [x] Keep compaction scoped to the main v2 agent session only

## Out of Scope for V1

- Continuous mode
- Non OpenAI-compatible providers
- Legacy interoperability
- MessageArtifact-based workflows in v2
- Full bash emulation
- Shell pipes/redirections/globbing/heredocs
- WebDAV shell surface unless a clean CLI model is defined later
- Langfuse
