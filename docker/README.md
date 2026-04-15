# Nova - Docker reference

Using Docker Compose is the recommended way to run Nova (including development).

## Project layout

```
Nova
├─ docker/
|  ├─ nginx/
|  ├─ ollama/
|  ├─ searxng/
|  ├─ .env
|  ├─ .env.example
|  ├─ docker-compose.base.yml
|  ├─ docker-compose.yml
|  ├─ docker-compose.dev.yml
|  ├─ docker-compose.from-source.yml
|  ├─ docker-compose.add-*.yml
|  └─ README.md
└─ ...
```

## One-time stack selection with `COMPOSE_FILE`

All optional modules are selected in `docker/.env` through `COMPOSE_FILE`.
Once selected, use only standard commands:

```bash
docker compose pull
docker compose up -d
docker compose down
```

Default in `.env.example`:

```dotenv
COMPOSE_FILE=docker-compose.yml
```

### Preset examples

Use one of these values in `docker/.env`:

- Base stack:
  - `COMPOSE_FILE=docker-compose.yml`
- Base + SearXNG:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-searxng.yml`
- Base + exec-runner:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-exec-runner.yml`
- Base + SearXNG + exec-runner:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-searxng.yml:docker-compose.add-exec-runner.yml`
- Base + Ollama:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-ollama.yml`
- Base + llama.cpp:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-llamacpp.yml`
- Base + llama.cpp embeddings:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-llamacpp-embeddings.yml`
- Development:
  - `COMPOSE_FILE=docker-compose.dev.yml`
- Development + exec-runner:
  - `COMPOSE_FILE=docker-compose.dev.yml:docker-compose.add-exec-runner.yml:docker-compose.add-exec-runner.dev.yml`
- Build from source:
  - `COMPOSE_FILE=docker-compose.from-source.yml`
- Build from source + exec-runner:
  - `COMPOSE_FILE=docker-compose.from-source.yml:docker-compose.add-exec-runner.yml:docker-compose.add-exec-runner.from-source.yml`

Note:
- On macOS/Linux, separate compose files with `:`.
- On Windows, separate compose files with `;`.

## Quickstart

1. Clone and enter the Docker directory:

```bash
git clone https://github.com/AMairesse/Nova.git
cd Nova/docker
cp .env.example .env
```

2. Set `COMPOSE_FILE` in `.env` to the stack you want.

3. Start containers:

```bash
docker compose up -d
```

4. Open Nova at `http://localhost:${HOST_PORT}` (default: `http://localhost:8080`).

5. Useful commands:

```bash
docker compose pull
docker compose up -d
docker compose down
```

If you changed `COMPOSE_FILE`, recreate services with:

```bash
docker compose up -d --remove-orphans
```

If an older deployment is already stuck on `502 Bad Gateway` after recreating `web`,
`docker compose restart nginx` is a temporary workaround while updating to the
newer dynamic upstream configuration.

## Available optional modules

- `docker-compose.add-searxng.yml`
  - Enables the deployment-default `Search` backend in Nova.
  - Users can still add their own custom remote search backends.
  - Requires `SEARXNG_SECRET` in `.env`.
- `docker-compose.add-exec-runner.yml`
  - Enables Nova's sandbox terminal backend for Python, package install, build workflows, and code-driven webapp generation.
  - Optional for Nova overall, but recommended for code-heavy workflows.
  - In the standard Docker setup, the only required `.env` value is `EXEC_RUNNER_SHARED_TOKEN`.
- `docker-compose.add-ollama.yml`
  - Starts Ollama and exposes a system provider in Nova.
- `docker-compose.add-llamacpp.yml`
  - Starts llama.cpp server and exposes a system provider in Nova.
- `docker-compose.add-llamacpp-embeddings.yml`
  - Starts llama.cpp embeddings server for memory embeddings.
- `docker-compose.add-pgadmin.yml`
  - Adds pgAdmin on port `5050`.

## Development stack

Set this in `docker/.env`:

```dotenv
COMPOSE_FILE=docker-compose.dev.yml
```

Then run:

```bash
docker compose up -d --build
```

To add the sandbox runner in development:

```dotenv
COMPOSE_FILE=docker-compose.dev.yml:docker-compose.add-exec-runner.yml:docker-compose.add-exec-runner.dev.yml
```

Access:

- Nova via Nginx: `http://localhost:${HOST_PORT}` (default `8080`)
- Direct ASGI app (dev helper): `http://localhost:8000`
- Debugpy web: `localhost:5678`
- Debugpy celery worker: `localhost:5679`
- Debugpy celery beat: `localhost:5680`

## Build from Source Stack

Set this in `docker/.env`:

```dotenv
COMPOSE_FILE=docker-compose.from-source.yml
```

Then run:

```bash
docker compose up -d --build
```

To add the sandbox runner while building Nova from source:

```dotenv
COMPOSE_FILE=docker-compose.from-source.yml:docker-compose.add-exec-runner.yml:docker-compose.add-exec-runner.from-source.yml
```

## Environment variables

Nova uses `docker/.env` for runtime configuration.

Core settings:

- `COMPOSE_FILE`: compose stack selection
- `DB_USER`, `DB_PASSWORD`
- `DJANGO_SUPERUSER_*`
- `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`
- `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- `FIELD_ENCRYPTION_KEY`, `DJANGO_SECRET_KEY`
- `HOST_PORT`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`

For production deployments behind a real domain, `ALLOWED_HOSTS` must include the public hostname and `CSRF_TRUSTED_ORIGINS` must include the matching `https://` origin. If those stay on `localhost`, managed OAuth callback URLs and WebSocket streaming will not work correctly.

Optional module settings:

- SearXNG: `SEARXNG_SECRET`
- Ollama: `OLLAMA_MODEL_NAME`, `OLLAMA_CONTEXT_LENGTH`
- llama.cpp: `LLAMA_CPP_MODEL`, `LLAMA_CPP_CHAT_TEMPLATE`, `LLAMA_CPP_CTX_SIZE`, `LLAMA_CPP_THINKING_BUDGET`
- llama.cpp embeddings: `MEMORY_EMBEDDINGS_MODEL`

Optional global settings:

- `USERFILE_EXPIRATION_DAYS`
- `DEBUG`

Exec-runner settings:

- Required in the standard module setup:
  - `EXEC_RUNNER_SHARED_TOKEN`
- Optional tuning:
  - `EXEC_RUNNER_REQUEST_TIMEOUT_SECONDS`
  - `EXEC_RUNNER_SESSION_TTL_SECONDS`
  - `EXEC_RUNNER_SANDBOX_IMAGE`
  - `EXEC_RUNNER_SANDBOX_NO_NEW_PRIVILEGES`
  - `EXEC_RUNNER_SANDBOX_MEMORY_LIMIT_MB`
  - `EXEC_RUNNER_SANDBOX_CPU_LIMIT`
  - `EXEC_RUNNER_SANDBOX_PIDS_LIMIT`
  - `EXEC_RUNNER_MAX_SYNC_BYTES`
  - `EXEC_RUNNER_MAX_DIFF_BYTES`
- Advanced topology overrides only:
  - `EXEC_RUNNER_BASE_URL`
  - `EXEC_RUNNER_ENABLED`

Notes:

- `exec-runner` is optional at the infrastructure level and enabled through `docker-compose.add-exec-runner.yml`.
- Without `exec-runner`, Nova still works for its main product features, but it does not expose the default Python backend or advanced sandboxed code/build workflows.
- `exec-runner` is the only service that receives the Docker socket. `web` and `celery-worker` call it over an authenticated internal HTTP API.
- In the standard Docker module setup, `EXEC_RUNNER_SHARED_TOKEN` is the only exec-runner value you normally need to set in `.env`.
- The Docker module disables `EXEC_RUNNER_SANDBOX_NO_NEW_PRIVILEGES` by default for compatibility with hosts where the sandbox bootstrap shell cannot start under that hardening flag.

## Exec-runner troubleshooting

If an `exec-runner` sandbox gets stuck or keeps a warm session in a bad state, you may need to
remove the sandbox containers that were created by `exec-runner` itself before running a full
`docker compose down`.

List active sandbox containers:

```bash
docker ps --filter "name=^/nova-exec-"
```

Force-remove all sandbox containers created by `exec-runner`:

```bash
for id in $(docker ps -aq --filter "name=^/nova-exec-"); do
  docker rm -f "$id"
done
```

If you also want to remove their persistent session volumes:

```bash
for v in $(docker volume ls -q --filter "name=^nova-exec-session-"); do
  docker volume rm "$v"
done
```

Then you can stop the Nova stack normally:

```bash
docker compose down
```

This is useful when debugging warm sandbox sessions, package installs, or workspace state that
does not reset as expected between runs.
