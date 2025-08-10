# Nova

**Nova is a personal-AI workspace that puts privacy first.**

Instead of sending every prompt to a remote model, Nova lets you decide – transparently and at run-time – whether an agent should reason with a local LLM running on your own machine or delegate to a cloud model only when extra horsepower is really needed. The result is a flexible “best of both worlds” setup that keeps sensitive data on-prem while still giving you access to state-of-the-art capabilities when you want them.

- **Agent-centric workflow** – Create smart assistants (agents) and equip them with “tools” that can be simple Python helpers, calendar utilities, HTTP/APIs or even other agents. Agents can chain or delegate work to one another, allowing complex reasoning paths.
- **Bring-your-own models** – Connect to OpenAI or Mistral if the task is public, but switch to local back-ends such as Ollama or LM Studio for anything confidential. Each provider is configured once and can be reused by multiple agents.
- **Privacy by design** – API keys and tokens are stored encrypted; only the minimal data required for a given call ever leaves your machine.
- **Built-in tools** – Nova comes with a bunch of “builtin” tools for common tasks, like CalDav calendar queries, web surfing, date management and more to come !
- **Pluggable tools** – Besides built-in utilities, Nova can talk to external micro-services through the open MCP protocol or any REST endpoint, so your agents keep growing with your needs.
- **Human-in-the-loop UI** – A lightweight web interface lets you chat with agents, watch their progress in real time, and manage providers / agents / tools without touching code.
- **Asynchronous calls** – You can safely invoke agents from the UI, and they will run in the background so you can do other things at the same time.

In short, Nova aims to make “agents with autonomy, privacy and extensibility” a reality for everyday users – giving you powerful automation while keeping your data yours.

## Key Features

- ✅ Tool-aware agents: Agents can invoke builtin tools, remote REST/MCP services **or even other agents**.
- ✅ Local-first LLM routing: Decide per-agent which provider to use: OpenAI, Mistral, Ollama, LM Studio or any future backend. Local models are preferred for sensitive data; the switch is transparent for you.
- ✅ Live streaming: you will see tool calls and sub-agent calls in real time so you can follow what happens under the hood. Then the agent's response will be streamed.
- ✅ Plug-and-play MCP client: Connect to any Model Context Protocol server, cache its tool catalogue and call remote tools with automatic input validation.
- ✅ Multilingual & i18n-ready: All UI strings use Django translations; English only currently.
- ✅ Extensible by design: Drop a Python module exposing a `get_functions()` map and it instantly becomes a multi-function “builtin” tool.

## Table of Contents

- [Key Features](#key-features)
- [Environment Variables](#environment-variables)
- [Production Deployment (Docker)](#production-deployment-docker)
- [Development Setup](#development-setup)
- [Project Layout](#project-layout)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)
- [Troubleshooting](#troubleshooting)

## Environment Variables

Nova uses a `.env` file for configuration. Copy `.env.example` to `.env` and edit as needed. Key variables include:

- **Required for Production (Docker):**

  - `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` (PostgreSQL settings).
  - `DJANGO_SECRET_KEY`: Random secure key.
  - `FIELD_ENCRYPTION_KEY`: For encrypting sensitive data.
  - `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD`, `DJANGO_SUPERUSER_EMAIL` (needed to auto-create admin user on first run).

- **Note:**
  - `DB_ENGINE`: Set to `postgresql` for prod (default in Docker), or `sqlite` for dev.
  - `REDIS_HOST`: Set to `redis` for Docker, use 127.0.0.1 and start a local Redis server for dev.

**Security Note:** Never commit `.env` to version control. Use strong, unique values.

## Production Deployment (Docker)

This is the recommended way to run Nova for real use, using Docker with PostgreSQL for better concurrency and persistence.

### Prerequisites

- Docker and Docker Compose installed.
- A `.env` file (see [Environment Variables](#environment-variables)).

### Steps

1. Clone the repo:

   ```
   git clone https://github.com/AMairesse/nova.git
   cd nova
   ```

2. Copy and configure `.env`:

   ```
   cp .env.example .env
   ```

   - Set DB vars (e.g., `DB_PASSWORD=secret`).
   - Set `DB_ENGINE=postgresql` (default in `docker-compose.yml`).
   - Add `DJANGO_SUPERUSER_*` vars for auto-admin creation.
   - Ensure volumes are configured for persistence (see `docker-compose.yml`).

3. Build and start containers:

   ```
   docker compose up -d --build
   ```

4. Access the app at `http://localhost:80` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

5. View logs:

   ```
   docker compose logs -f
   ```

6. Stop/restart:

   ```
   docker compose down
   docker compose up -d
   ```

7. Updates:

   ```
   git pull
   docker compose up -d --build
   ```

### Notes

- **Persistence:** Docker volumes (`postgres_data`, `static_data`, `media_data`) ensure data survives container restarts. Back them up regularly.
- **Dev vs Prod:** Prod uses PostgreSQL with a persistent volume. Dev uses SQLite (local file). Switch via `DB_ENGINE` in `.env`.
- **Security:** Use strong secrets. Expose via a reverse-proxy (e.g., Nginx) with HTTPS. Don't commit `.env`.
- **Tip:** For local inference, install [Ollama](https://ollama.com/) and load a model like `llama3`. Add it as a provider in the UI (Type: Ollama, Model: `llama3`, Base URL: `http://host.docker.internal:11434/` or your host IP).

## Development Setup

For development or testing only (uses SQLite, less scalable). Not recommended for production due to concurrency limits.

1. Clone and setup virtual env:

   ```
   git clone https://github.com/AMairesse/nova.git
   cd nova
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Launch a redis server:

   ```
   redis-server
   ```

4. Configure `.env` (from `.env.example`):

   - Set `DB_ENGINE` to `sqlite` or remove all `DB_*` from `.env` file
   - Fill `DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY`.
   - Fill `REDIS_HOST`, `REDIS_PORT` for your local Redis server.

5. Run migrations and create superuser:

   ```
   python manage.py migrate
   python manage.py createsuperuser
   ```

6. Launch dev server:

   ```
   daphne -b 0.0.0.0 -p 8000 nova.asgi:application
   ```

7. Open `http://localhost:8000`, log in, and configure via **Config › LLM Providers**.

**Tip:** For local models, use Ollama as above.

## Project Layout

```
nova/
├─ api/ # Minimal REST facade
├─ mcp/ # Thin wrapper around FastMCP
├─ migrations/ # Django model migration scripts
├─ static/ # JS helpers (streaming, tool modal manager…)
├─ templates/ # Django + Bootstrap 5 UI
├─ tools/ # Built-in tool modules (CalDav, agent wrapper…)
└─ views/ # Django views
```

## Roadmap

~~ 1. Add a "internet search" tool and a "web browser" tool ~~

2. Management of "thinking models"

3. File management : add a file, receive a file as a result, file support for MCP tools, ...

4. Add a scratchpad tool (acting like a memory for long task)

5. Add a canvas tool (acting like a UI component for the agent to interact with the user)

## Contributing

Pull requests are welcome! To propose a tool:

1. Create a new Python file under `nova/tools/your_tool.py`.

2. Expose either
   - a single callable (for simple tools), or
   - a `get_functions()` dict for multi-function tools.

3. Add metadata to `nova/tools/__init__.py` so it appears in the “Create Tool” modal.

4. Write unit tests under `tests/`.

Please run `pre-commit install` to apply linting and type checks before submitting.

## License

Nova is released under the MIT License – see `LICENSE` for details.

## Acknowledgements

- [Django](https://www.djangoproject.com/) – the rock-solid web framework
- [LangChain](https://python.langchain.com/) – agent & tool abstractions
- [FastMCP](https://github.com/modelcontext/fastmcp) – open protocol for tool servers
- [Bootstrap 5](https://getbootstrap.com/) – sleek, responsive UI components

Made with ❤️ and a healthy concern for data privacy.

## Troubleshooting

- **Port conflicts:** Ensure ports 80 (Nginx), 8000 (Daphne), and 5432 (PostgreSQL) are free. Stop conflicting services or edit `docker-compose.yml`.
- **DB not ready:** If web container fails with DB errors, check PostgreSQL logs (`docker compose logs db`). Increase healthcheck timeouts if needed.
- **No superuser:** Set `DJANGO_SUPERUSER_*` in `.env` and restart. Or run `docker compose exec web python manage.py createsuperuser`.
- **Ollama unreachable:** Use `host.docker.internal` (Docker Desktop) or your machine's IP for Base URL. Ensure Ollama runs on the host.
- **Volumes lost data?** Back up volumes with `docker volume ls` and tools like `docker-volume-backup`.

For more help, open an issue on GitHub.
