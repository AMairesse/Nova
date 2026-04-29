# Nova - Development Guide

## Core Architecture

Nova is a Django application with a single agent runtime centered on `nova/runtime/`.

Important backend slices:

- `nova/runtime/`: Nova runtime, provider client, VFS, task executor
- `nova/runtime/commands/`: progressively extracted terminal command helpers
- `nova/exec_runner/`: optional sandbox terminal backend and runner client/server pieces
- `nova/plugins/`: internal plugin registry for builtins/system capabilities
- `nova/providers/`: provider-specific model discovery and capability handling
- `nova/threads/`: thread lifecycle domain services
- `nova/continuous/`: continuous-mode summaries, recall, and context builder
- `nova/tasks/`: Celery tasks, scheduled task execution, maintenance flows
- `nova/web/`: web search, browsing, and downloads
- `nova/webapp/`: live web app publication and serving
- `user_settings/`: providers, capabilities/connections, agents, memory, and tasks UI

## Repository Layout

```text
Nova
├─ docker/                      # Docker stacks and env configuration
├─ locale/                      # Django translations
├─ nova/
│  ├─ api/                      # Minimal REST facade
│  ├─ continuous/               # Continuous-mode context and maintenance
│  ├─ exec_runner/              # Optional sandbox terminal backend
│  ├─ memory/                   # Memory document/chunk services
│  ├─ mcp/                      # MCP client and managed OAuth support
│  ├─ models/                   # One model per file
│  ├─ plugins/                  # Internal plugin descriptors and shared helpers
│  ├─ providers/                # Provider adapters and capability logic
│  ├─ runtime/                  # Nova runtime
│  │  └─ commands/              # Terminal command helpers
│  ├─ static/                   # JS/CSS assets
│  ├─ tasks/                    # Celery tasks and task templates
│  ├─ templates/                # Django templates
│  ├─ tests/                    # Django test suite
│  ├─ threads/                  # Thread lifecycle services
│  ├─ views/                    # Django views
│  ├─ web/                      # Search/browser/download services
│  └─ webapp/                   # Live webapp publication
├─ user_settings/               # Settings app
├─ screenshots/
├─ plans/
├─ README-agents.md
├─ README-dev.md
└─ README.md
```

## Data Model Landmarks

### Conversations and Tasks

- `Thread`: conversation container
- `Message`: persisted conversation turn or system/runtime event
- `Task`: runtime execution state
- `Interaction`: blocking user clarification for `ask_user`
- `AgentThreadSession`: runtime session/compaction state

### Files

- `UserFile(scope=THREAD_SHARED)`: durable thread files
- `UserFile(scope=MESSAGE_ATTACHMENT)`: user message attachments and message-scoped file outputs

Thread files, attachments, and message-scoped outputs are all represented through `UserFile`.

### Memory

Long-term memory is modeled with:

- `MemoryDirectory`
- `MemoryDocument`
- `MemoryChunk`
- `MemoryChunkEmbedding`

`/memory` is projected from these tables and is shared per user.

### Continuous Mode

Continuous mode is built from:

- `DaySegment`
- `DaySegmentEmbedding`
- `TranscriptChunk`
- `TranscriptChunkEmbedding`

Continuous execution relies on stored day segments, summaries, and transcript chunks.

## Runtime Model

The model-facing surface is intentionally small:

- `terminal(command: str)`
- `delegate_to_agent(...)`
- `ask_user(...)`

Everything else is exposed through terminal commands, virtual files, or attached integrations.

Examples:

- current-message inputs: `/inbox/...`
- earlier live-message attachments: `/history/...`
- mail: `mail ...`
- calendar: `calendar ...`
- memory: `grep ...`, `memory search ...`
- Python backend: `python ...` via `exec-runner` when enabled
- webapp: `webapp expose ...`
- MCP: `mcp ...`
- API tools: `api ...`

## Internal Plugins

Builtins and system capabilities are registered in `nova/plugins/`.

Each internal plugin describes:

- metadata/settings
- builtin subtype mapping
- capability family / selection behavior
- runtime capability resolution
- skill docs
- optional connection-test hook

`Tool.tool_subtype` remains the persisted product-facing selector, but the registry in
`nova/plugins/registry.py` is the source of truth for builtin behavior.

At the product level, the registry distinguishes:

- built-in capabilities available by default
- backend-backed capabilities where an agent selects one backend (`Search`, `Python`)
- multi-instance user connections such as mail, calendar, WebDAV, MCP, and API

Current nuance:

- `Search` can use a deployment default and optional user-defined custom backends
- `Python` currently uses the deployment-default sandbox backend when `exec-runner` is enabled

## Provider-Aware Behavior

- `LLMProvider.capability_profile` is the persisted source of model capability state
- metadata refresh and active verification are separate flows
- providers verified with `tools=unsupported` are gated correctly in agent/task UX
- provider adapters normalize chat, native media responses, and explicit streaming modes
- MCP managed OAuth is handled in settings and refreshed silently at runtime when possible

## Local Environment

Activate the virtualenv before Python commands:

```bash
. .venv/bin/activate
```

Recommended test command:

```bash
DEBUG=False \
CSRF_TRUSTED_ORIGINS='https://localhost,https://testserver' \
DJANGO_SETTINGS_MODULE=nova.settings_test \
python manage.py test
```

Targeted test example:

```bash
DEBUG=False \
CSRF_TRUSTED_ORIGINS='https://localhost,https://testserver' \
DJANGO_SETTINGS_MODULE=nova.settings_test \
python manage.py test user_settings.tests.test_tasks_views
```

Quick syntax check:

```bash
python -m py_compile user_settings/views/tasks.py
```

## Translation

Update messages with:

```bash
python manage.py makemessages -l en
python manage.py makemessages -l en --domain djangojs
```

## Dependencies

Requirements are defined in `pyproject.toml` / `requirements.txt`.

Useful checks:

```bash
deptry .
pip-compile --upgrade pyproject.toml --output-file=requirements.txt
```

## Vendorized Frontend Assets

Check embedded frontend assets:

```bash
./scripts/check_vendor_assets.sh
./scripts/check_vendor_assets.sh --strict
./scripts/check_vendor_assets.sh --local-only
```

Update them manually:

```bash
./scripts/update_vendor_assets.sh
./scripts/update_vendor_assets.sh --bootstrap 5.3.7 --bootstrap-icons 1.11.3 --htmx 2.0.6
```

## WebApp Authoring Pattern

The live webapp workflow is:

1. create/edit files in the normal workspace
2. publish with `webapp expose <source_dir>`
3. keep editing the same source files; the published app updates live

Published webapps are served with a sandbox CSP and iframe sandbox that omit
`allow-same-origin`. In production, set `WEBAPP_PUBLIC_ORIGIN` so user-authored
HTML/JS runs on a dedicated origin rather than the authenticated Nova origin.

## Outbound Network Policy

Tenant-controlled outbound URLs and host/port pairs should go through
`nova/web/network_policy.py` and, for HTTP, `nova/web/safe_http.py`.

The default policy blocks local/private/internal destinations and revalidates
redirects. Use `NOVA_EGRESS_ALLOWLIST` for admin-approved internal integrations;
do not add connector-specific SSRF filters unless the shared policy cannot
represent the protocol.
