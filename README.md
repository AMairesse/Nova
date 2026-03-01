# Nova

[![Docker Image CI](https://github.com/AMairesse/Nova/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AMairesse/Nova/actions/workflows/docker-image.yml)
[![Django CI](https://github.com/AMairesse/Nova/actions/workflows/django.yml/badge.svg)](https://github.com/AMairesse/Nova/actions/workflows/django.yml)

**Nova is a privacy-first, multi-tenant AI agent workspace.**

![Get the news](./screenshots/Webbrowsing%20by%20agent.png)

| | | | |
| --- | --- | --- | --- |
| ![Your agent can use CalDav](./screenshots/Caldav%20use.png) | ![Work on files](./screenshots/Work%20on%20files.png) | ![Define your caldav agent](./screenshots/Define%20your%20caldav%20agent.png) | ![Define your main agent](./screenshots/Define%20your%20main%20agent.png) |
| ![Providers' config](./screenshots/Providers%20config.png) | ![Caldav config](./screenshots/Caldav%20config.png) | ![Multiple tools](./screenshots/Multiple%20tools.png) | ![Agents and Agents as tools](./screenshots/Various%20agents.png) |
| | | | |

## Quickstart

Quickstart on your computer (Docker):

```bash
git clone https://github.com/AMairesse/Nova.git
cd Nova/docker
cp .env.example .env
docker compose up -d
```

Open [http://localhost:8080](http://localhost:8080).

Default credentials:
- Username: `admin`
- Password: `changeme`

Configure optional modules in `docker/.env` using `COMPOSE_FILE`.

Then configure your agents: [How to configure agents](README-agents.md).

## Table of Contents

- [What Nova Is](#what-nova-is)
- [Key Capabilities](#key-capabilities)
- [Docker Setup](#docker-setup)
- [API](#api)
- [Project Layout](#project-layout)
- [Documentation Map](#documentation-map)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)
- [Troubleshooting](#troubleshooting)

## What Nova Is

Nova is a web platform to build and run user-scoped AI agents with strong privacy and extensibility guarantees.

- Agent-centric orchestration: each user can create specialized agents and compose them as tools.
- Local or remote model routing: choose per-provider and per-agent what model backend to use.
- Real-time execution flow: tool calls and agent progress are streamed live in the UI.
- Multi-tenancy by design: data access is scoped to the authenticated user.
- Background runtime: long operations run asynchronously through Celery.

## Key Capabilities

### Conversation Modes

Nova supports two conversation experiences:

- `Threads` mode for classic conversation threads.
- `Continuous` mode for day-segmented ongoing discussion with summaries and recall.

Both modes coexist in the UI and can be used side by side.

### Skills and Tools

Agents can use:

- Built-in tools (browser, memory, date/time, webapp, email, caldav, webdav, code execution, etc.).
- API and MCP tools.
- Other agents as tools.

Nova also supports on-demand skills in the runtime (`list_skills`, `load_skill`) so some tool families can be activated only when needed for the current turn.

### Automation and Scheduled Tasks

Nova includes user-managed scheduled tasks in Settings:

- Trigger types: cron and email polling.
- Run modes: new thread, continuous message, ephemeral.
- Predefined task templates (including guided setup flows for selected templates).
- Maintenance tasks (for example continuous nightly day summaries).

### Structured Memory

Long-term memory is structured and queryable:

- Memory items and themes are user-scoped.
- Builtin memory tool supports search/add/get/archive/list themes.
- Optional semantic retrieval with embeddings is available through per-user settings.

### Files and Web Apps

- User files are scoped per user/thread and stored in MinIO.
- Agents can generate static mini web apps and expose them under `/apps/<slug>/`.
- Web app serving enforces user ownership checks.

## Docker Setup

Docker is the recommended setup for production-like and development usage.

See [docker/README.md](docker/README.md) for:

- Stack selection via `COMPOSE_FILE`
- Optional modules
- Development and source builds
- Environment variables

## API

Nova exposes a minimal authenticated API.

### Endpoints

- `GET /api/` -> API root (discovery)
- `GET /api/ask/` -> usage information
- `POST /api/ask/` -> ask a question to your default agent

### Authentication

Use token authentication:

1. Generate an API token in **Settings > General**.
2. Send `Authorization: Token <YOUR_TOKEN>`.

### Example

```bash
curl -H "Authorization: Token YOUR_TOKEN_HERE" \
     -H "Content-Type: application/json" \
     --data '{"question":"Who are you and what can you do?"}' \
     http://localhost:8080/api/ask/
```

### Notes

- A default agent must be configured for the authenticated user.
- Invalid or missing token returns `401 Unauthorized`.
- Missing default agent returns `400`.

## Project Layout

```text
Nova
├─ docker/                # Docker stacks and runtime configuration
├─ nova/
│  ├─ api/                # REST API endpoints
│  ├─ continuous/         # Continuous-mode context and conversation tools
│  ├─ llm/                # Agent runtime, prompts, middleware, skill policy
│  ├─ models/             # One model per file
│  ├─ tasks/              # Celery tasks and task templates
│  ├─ tools/              # Built-in tools, tool loading, agent-tool wrappers
│  ├─ views/              # App views (threads, continuous, files, tasks...)
│  ├─ templates/          # Django templates
│  └─ static/             # Frontend assets
├─ user_settings/         # User configuration app (providers, agents, tools, memory, tasks)
├─ plans/                 # Design and architecture plans
├─ README-agents.md       # Agent configuration guide
└─ README-dev.md          # Development-oriented repository guide
```

## Documentation Map

- [docker/README.md](docker/README.md): Docker stacks and environment configuration
- [README-agents.md](README-agents.md): Recommended agent/tool setup
- [README-dev.md](README-dev.md): Development structure and internals
- [plans/continuous_discussion.md](plans/continuous_discussion.md): Continuous mode decisions
- [plans/memory.md](plans/memory.md): Memory system decisions

## Roadmap

### Recently shipped

1. Continuous mode with day segments, summaries, and conversation recall tools.
2. Structured long-term memory with optional embeddings.
3. On-demand skill loading in the runtime.
4. Scheduled tasks with templates and maintenance flows.

### Planned

1. Messaging app integrations (Signal, Discord, ...).
2. Agent self-scheduling capabilities.

## Contributing

Pull requests are welcome.

## License

Nova is released under the MIT License. See [LICENSE](LICENSE).

## Acknowledgements

- [Django](https://www.djangoproject.com/)
- [Django REST Framework](https://www.django-rest-framework.org/)
- [Django Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [LangChain](https://python.langchain.com/)
- [LangGraph](https://www.langchain.com/langgraph)
- [FastMCP](https://github.com/modelcontext/fastmcp)
- [Bootstrap 5](https://getbootstrap.com/)

## Troubleshooting

- **Port conflicts:** Ensure your configured `HOST_PORT` (default `8080`) is free.
- **Stack mismatch:** If you changed `COMPOSE_FILE`, run `docker compose up -d --remove-orphans`.
- **DB startup issues:** Check database container health/logs and wait for healthchecks.
- **No superuser:** Ensure `DJANGO_SUPERUSER_*` is set in `docker/.env`.
- **Optional module not visible in Nova:** Verify the related compose add-on is included in `COMPOSE_FILE` and required env vars are set.
- **Ollama connectivity issues:** Use `host.docker.internal` (Docker Desktop) or host IP when targeting host-side Ollama.

For more help, open an issue on GitHub.
