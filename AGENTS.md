# AGENTS.md

Operational guidance for contributors and coding agents working in this repository.

## 1) Project Snapshot

- Product: Nova, a multi-tenant AI agent platform focused on privacy and extensibility.
- Backend: Django + Django REST Framework + Django Channels.
- Async runtime: Celery + Redis.
- Storage: PostgreSQL + MinIO.
- Agent runtime: Nova runtime centered on `terminal(...)`, `delegate_to_agent(...)`, and `ask_user(...)`, with plugin-backed capabilities and explicit sub-agent delegation.

Primary code areas:
- Core app: `nova/`
- User settings app: `user_settings/`
- Entrypoint: `manage.py`

## 2) Architecture Essentials

- HTTP + WebSocket served by ASGI (`Daphne`) behind `nginx` in Docker deployments.
- Real-time flow: request creates Celery task, task streams progress through Redis channel layer, UI updates over WebSocket.
- Multi-tenancy: all user data is user-scoped in models and queries.
- Secrets: API keys are encrypted at rest.
- Files: stored in MinIO under user/thread-scoped paths.
- Optional sandbox terminal: `exec-runner` can provide the deployment-default Python/code backend and warm sandbox sessions when enabled in Docker.
- Provider runtime is provider-aware:
  - main orchestration stays in `nova/runtime/`
  - provider-specific behavior lives in `nova/providers/`
  - shared embeddings / hybrid-search helpers live in `nova/llm/`
  - `LLMProvider.capability_profile` is the single persisted source of model capabilities and verification state
- Files and message-scoped outputs are represented through `UserFile`:
  - `UserFile(scope=THREAD_SHARED)` stores durable thread files
  - `UserFile(scope=MESSAGE_ATTACHMENT)` stores current-message attachments and message-scoped outputs/scratch files
  - message-scoped non-file metadata lives in `Message.internal_data`
- Built-in/system capabilities are resolved through `nova/plugins/`.
- Tool-less execution is a first-class runtime mode for providers/models that do not support tools.

## 3) Current Feature Baselines to Preserve

### Skills middleware (tool-based skills)

- Builtin skill classification is defined in module metadata (`METADATA.loading`).
- Skills are activated explicitly (`load_skill`) and visibility is turn-scoped.
- Keep email aggregation behavior unchanged when evolving skill loading.

### Continuous discussion mode

- Continuous mode and thread mode coexist.
- Continuous conversation recall tools are system capabilities from `nova/continuous/tools/conversation_tools.py` (not user-addable builtins).
- Continuous mode uses persisted messages, day segments, summaries, transcript chunks, and runtime compaction state; avoid regressions in context reconstruction behavior.

### Provider-aware multimodal behavior

- Preserve the distinction between:
  - provider metadata refresh
  - active verification
  - effective capability gating from `capability_profile`
- OpenRouter and LM Studio have provider-aware model discovery; do not regress the “connection first, model second” flow.
- Message media should appear as `UserFile` attachments or message-scoped outputs in the thread and remain usable from `Files` and Markdown VFS references.
- Main agents may delegate files to sub-agents; child outputs must remain recoverable by the parent agent under `/subagents/...`.
- Models verified as `tools=unsupported` must not receive default Nova tools unless the run is explicitly tool-less.

## 4) Repository Conventions

- Models: one model per file under `nova/models/`.
- Internal plugins: under `nova/plugins/`.
- Runtime orchestration: under `nova/runtime/`.
- Provider-specific logic: under `nova/providers/`.
- Shared embeddings / hybrid search helpers: under `nova/llm/`.
- Celery tasks: in `nova/tasks/`.
- Views: feature-grouped under `nova/views/` and `user_settings/views/`.
- Avoid editing vendored or minified artifacts.

## 5) Local Environment

Always activate virtualenv before Python commands:

```bash
. .venv/bin/activate
```

## 6) Test and Validation Commands

Use stable test env vars to avoid local `.env` debug side effects:

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

Useful search pattern:

```bash
rg "TaskDefinition|EMAIL_POLL|email_tool" nova user_settings
```

If Docker services are not running locally, prefer test settings for management commands:

```bash
python manage.py migrate --settings nova.settings_test
python manage.py test --settings nova.settings_test
```

## 7) Contribution Rules

- Branch naming: prefix feature branches with `codex/`.
- Prefer focused changes and targeted tests near modified files.
- Preserve backwards-compatible behavior unless change is explicitly requested.
- For UX behavior changes, prefer non-blocking warnings when action should remain allowed.

## 8) Security and Data Handling

- Never commit credentials or tokens.
- Do not read or modify `.env` in automated edits.
- Maintain tenant isolation assumptions in every query/path.

## 9) Recommended Reference Docs

- `docker/README.md` for Docker stack selection and optional modules such as `exec-runner`.
- `README-dev.md` for development structure.
- `README-agents.md` for functional agent setup.
- `README.md` for product-level capabilities and quickstart.
- `user_settings/README.md` for provider/agent settings UI behavior.
- `plans/continuous_discussion.md` for continuous mode decisions.
- `plans/memory.md` for memory system design context.
