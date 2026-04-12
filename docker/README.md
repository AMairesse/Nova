# Nova - Docker reference

Using Docker Compose is the recommended way to run Nova (including development).

## Project layout

```
Nova
├─ docker/
|  ├─ judge0/
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
- Base + Judge0:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-judge0.yml`
- Base + SearXNG + Judge0:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-searxng.yml:docker-compose.add-judge0.yml`
- Base + Ollama:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-ollama.yml`
- Base + llama.cpp:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-llamacpp.yml`
- Base + llama.cpp embeddings:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.add-llamacpp-embeddings.yml`
- Development:
  - `COMPOSE_FILE=docker-compose.dev.yml`
- Build from source:
  - `COMPOSE_FILE=docker-compose.from-source.yml`

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
- `docker-compose.add-judge0.yml`
  - Enables the deployment-default `Python` backend in Nova.
  - Users can still add their own custom remote Python backends.
  - Requires host cgroups configuration (see Judge0 upstream docs for v1.13+).
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

Optional module settings:

- SearXNG: `SEARXNG_SECRET`
- Ollama: `OLLAMA_MODEL_NAME`, `OLLAMA_CONTEXT_LENGTH`
- llama.cpp: `LLAMA_CPP_MODEL`, `LLAMA_CPP_CHAT_TEMPLATE`, `LLAMA_CPP_CTX_SIZE`, `LLAMA_CPP_THINKING_BUDGET`
- llama.cpp embeddings: `MEMORY_EMBEDDINGS_MODEL`

Optional global settings:

- `USERFILE_EXPIRATION_DAYS`
- `DEBUG`
