# Nova - Agent Setup Guide

This guide explains how to configure providers, tools, and agents for the current React Terminal runtime.

## Mental Model

Nova agents do not receive a large catalog of callable tools. They work through:

- `terminal(...)`
- `delegate_to_agent(...)`
- `ask_user(...)`

Most capabilities are exposed through terminal commands, virtual files, and attached integrations.

## 1. Configure a Provider

You need at least one `LLMProvider`.

Provider configuration is provider-aware:

1. save the connection
2. load/select a model when the provider supports discovery
3. refresh metadata
4. run active verification

Recommended rule:

- main agents and tool-using sub-agents should use a model verified with tool support
- providers verified without tool support are better suited to simple chat or specialized media agents

### Example Local Provider

| Field | Value |
| --- | --- |
| Name | `LM Studio - Main` |
| Type | `LMStudio` |
| Model | `Select from catalog or enter manually` |
| Base URL | `http://host.docker.internal:1234/v1` |
| Max context tokens | `50000` |

### Example Remote Provider

| Field | Value |
| --- | --- |
| Name | `OpenRouter - GPT-5-mini` |
| Type | `OpenRouter` |
| Model | `openai/gpt-5-mini` |
| API key | `Your key` |
| Base URL | `https://openrouter.ai/api/v1` |
| Max context tokens | `400000` |

## 2. Configure Tools

Attach only the integrations you actually want an agent to use.

Common builtins:

- `Date / Time`
- `Browser`
- `Memory`
- `WebApp`
- `Email`
- `CalDAV`
- `WebDAV`

Optional system services:

- `SearXNG` if enabled in Docker
- `Judge0` if enabled in Docker

Optional external adapters:

- MCP servers
- custom API services with declared operations

Notes:

- `ask_user` is built into the runtime and is not configured as a tool
- email and calendar can be configured multiple times for one user
- MCP tools can use managed OAuth when the server requires it

### MCP Authentication

Nova supports:

- no authentication
- basic auth
- access token
- managed OAuth (`Connect with OAuth` / `Reconnect with OAuth`)

Use managed OAuth for MCP servers that return an OAuth challenge, such as You.com.

### Custom API Services

For API tools, define:

- the service endpoint
- auth mode
- one or more `APIToolOperation` entries

Each operation describes:

- HTTP method
- path template
- query parameters
- optional body parameter
- input/output schema

## 3. Recommended Agent Layout

Default setup usually works well with:

- one main agent: `Nova`
- one internet-oriented sub-agent: `Internet Agent`
- one coding-oriented sub-agent: `Python Agent`
- optionally one media/image sub-agent if you use a dedicated image-capable provider

### Main Agent

Suggested attached capabilities:

- `Date / Time`
- `Memory`
- `WebApp`
- one or more `Email`
- one or more `CalDAV`
- optional `WebDAV`
- optional `MCP`
- optional custom `API`

Suggested delegated sub-agents:

- `Internet Agent`
- `Python Agent`

### Internet Agent

Suggested attached capabilities:

- `Browser`
- `SearXNG` when available
- optional `Date / Time`

Use it for:

- web search
- browsing
- source gathering

### Python Agent

Suggested attached capabilities:

- `Judge0` when available

Use it for:

- data processing
- sandboxed Python/code execution

## 4. What the Runtime Exposes

Once configured, agents can use command families such as:

- files: `ls`, `cat`, `find`, `tee`, `mv`, `rm`, ...
- web: `search`, `browse`, `curl`, `wget`
- memory: `grep`, `memory search`
- mail: `mail ...`
- calendar: `calendar ...`
- web apps: `webapp expose`, `webapp list`, `webapp show`
- MCP: `mcp ...`
- custom API: `api ...`

They also see:

- `/skills` for guidance docs
- `/memory` when memory is attached
- `/webdav` when WebDAV is attached

## 5. Long-Term Memory

Memory is stored as Markdown files under `/memory`.

Examples:

- `/memory/profile.md`
- `/memory/projects/client-a.md`

Useful commands:

- `grep` for lexical search
- `memory search` for hybrid lexical + semantic search

## 6. Web Apps

Web apps are live publications from normal workspace files.

Workflow:

1. create files in the thread workspace
2. run `webapp expose <source_dir>`
3. keep editing those same files

The published app stays linked to the source directory.

## 7. Continuous Mode

Nova supports:

- classic threads
- one continuous user-scoped discussion with day summaries and recall

Agents in continuous mode use the same runtime and gain access to `history search` and `history get`.

## 8. Quick Test Scenarios

- Internet: ask for a recent fact and ensure the agent uses `search` / `browse`
- Mail: ask to list recent emails
- Calendar: ask for next week’s events
- Memory: ask the agent to remember a preference, then retrieve it later
- WebApp: ask it to create a static app, then expose it
- MCP/API: call a known external operation and save the result to a file
