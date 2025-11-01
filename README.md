# Nova

[![Docker Image CI](https://github.com/AMairesse/Nova/actions/workflows/docker-image.yml/badge.svg)](https://github.com/AMairesse/Nova/actions/workflows/docker-image.yml)
[![Django CI](https://github.com/AMairesse/Nova/actions/workflows/django.yml/badge.svg)](https://github.com/AMairesse/Nova/actions/workflows/django.yml)

**Nova is a personal‑AI workspace that puts privacy first.**

![Tool being used](./screenshots/Tool%20being%20used.png)

| | | | |
| --- | --- | --- | --- |
| ![Providers' config](./screenshots/Providers%20config.png) | ![MCP Servers support](./screenshots/MCP%20Servers%20support.png) | ![Define your caldav agent](./screenshots/Define%20your%20caldav%20agent.png) | ![Define your main agent](./screenshots/Define%20your%20main%20agent.png) |
| ![Simple question](./screenshots/Simple%20question.png) | ![Your agent can use CalDav](./screenshots/Caldav%20use.png) | ![Webbrowsing by agent](./screenshots/Webbrowsing%20by%20agent.png) | ![Agents and Agents as tools](./screenshots/Agents%20and%20Agents%20as%20tools.png) |
| | | | |

## Quickstart

Quickstart on your computer (with Docker):

```
git clone https://github.com/AMairesse/Nova.git
cd Nova/docker
cp .env.example .env
docker compose up -d
```

Open [http://localhost:8080](http://localhost:8080)

The default username is `admin` and the default password is `changeme`.

Then you can create your first agent and start playing with it : [How to configure agents](README-agents.md).

<details>
  <summary>Optionally you can use Nova with llama.cpp included</summary>
  If you also want to use llama.cpp for a default system provider available to all users, you can use the `docker-compose.add-llamacpp.yml` file:

```
docker compose -f docker-compose.yml -f docker-compose.add-llamacpp.yml up -d
```

See [docker/README.md](docker/README.md#add-llamacpp-to-your-default-setup) for more details.
</details>

## Description

Instead of sending every prompt to a remote model, Nova lets you decide – transparently and at run‑time – whether an agent should reason with a local LLM running on your own machine or delegate to a cloud model only when extra horsepower is really needed. The result is a flexible “best of both worlds” setup that keeps sensitive data on‑prem while still giving you access to state‑of‑the‑art capabilities when you want them.

- **Agent‑centric workflow** – Create smart assistants (agents) and equip them with “tools” that can be simple Python helpers, calendar utilities, HTTP/APIs or even other agents. Agents can chain or delegate work to one another, allowing complex reasoning paths.
- **Bring‑your‑own models** – Connect to OpenAI (or compatible providers like openrouter.ai) or Mistral if the task is public, but switch to local back‑ends such as Ollama, llama.cpp or LM Studio for anything confidential. Each provider is configured once and can be reused by multiple agents.
- **Privacy by design** – API keys and tokens are stored encrypted; only the minimal data required for a given call ever leaves your machine.
- **Built‑in tools** – Nova comes with a bunch of “built‑in” tools for common tasks, like CalDav calendar queries, web surfing, date management and more to come!
- **Pluggable tools** – Besides built‑in utilities, Nova can talk to external micro‑services through the open MCP protocol or any REST endpoint, so your agents keep growing with your needs.
- **Human‑in‑the‑loop UI** – A lightweight web interface lets you chat with agents, watch their progress in real time, and manage providers / agents / tools without touching code.
- **Asynchronous calls** – You can safely invoke agents from the UI, and they will run in the background so you can do other things at the same time.
- **API available** – You can easily ask a question to your default agent using the API.

In short, Nova aims to make “agents with autonomy, privacy and extensibility” a reality for everyday users – giving you powerful automation while keeping your data yours.

## Key Features

- ✅ Tool‑aware agents: Agents can invoke built‑in tools, remote REST/MCP services **or even other agents**.
- ✅ Local‑first LLM routing: Decide per‑agent which provider to use: OpenAI, Mistral, Ollama, LM Studio or any future backend. Local models are preferred for sensitive data; the switch is transparent for you.
- ✅ Live streaming: you will see tool calls and sub‑agent calls in real time so you can follow what happens under the hood. Then the agent's response will be streamed.
- ✅ Plug‑and‑play MCP client: Connect to any Model Context Protocol server, cache its tool catalogue and call remote tools with automatic input validation.
- ✅ Multilingual & i18n‑ready: All UI strings use Django translations; English only currently.
- ✅ Extensible by design: Drop a Python module exposing a `get_functions()` map and it instantly becomes a multi‑function “built‑in” tool.

## Table of Contents

- [Key Features](#key-features)
- [Production Deployment (Docker)](#production-deployment-docker)
- [Development Setup (Docker)](#development-setup-docker)
- [API](#api)
- [Project Layout](#project-layout)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)
- [Troubleshooting](#troubleshooting)

## Production Deployment (Docker)

This is the recommended way to run Nova.
See the [Docker README.md](docker/) for details.

## Development Setup (Docker)

Development setup also uses Docker given the number of components involved.

See the [Docker README.md](docker/) for details.

## API

A simple API is available to ask a question to your default agent.

### How to use:
1. **Get your token** from the configuration screen.
2. **Send a POST request** to the API endpoint with your question.

#### Example using `curl`:

```bash
curl -H "Authorization: Token YOUR_TOKEN_HERE" \
     -H "Content-Type: application/json" \
     --data '{"question":"Who are you and what can you do?"}' \
     http://localhost:8080/api/ask/
```

### API Details
- **Method:** POST
- **Endpoint:** `http://localhost:8080/api/ask/`
- **Headers:**
  - `Authorization: Token YOUR_TOKEN_HERE`
  - `Content-Type: application/json`
- **Request body:**
  ```json
  {
    "question": "Your question here"
  }
  ```
- **Response:** The API returns a JSON object containing the agent's answer.

#### Example response:
```json
{
  "question": "Who are you?",
  "answer": "I am your default agent. I can answer your questions and assist you with various tasks."
}
```

**Notes:**
- Replace `YOUR_TOKEN_HERE` with your actual token.
- If your token is invalid or missing, the API will return a 401 Unauthorized error.

## Project Layout

```
Nova
├─ docker/ # Docker compose configuration for the project
├─ nova/
|  ├─ api/ # Minimal REST facade
|  ├─ mcp/ # Thin wrapper around FastMCP
|  ├─ migrations/ # Django model migration scripts
|  ├─ static/ # JS helpers (streaming, tool modal manager…)
|  ├─ templates/ # Django + Bootstrap 5 UI
|  ├─ tools/ # Built‑in tool modules (CalDav, agent wrapper…)
|  └─ views/ # Django views
├─ user_settings/ # Dedicated Django app for the user settings
```

## Roadmap
1. File management : add a file, receive a file as a result, file support for MCP tools, ...
2. Add a scratchpad tool (acting like a memory for long task)
3. Add a canvas tool (acting like a UI component for the agent to interact with the user)
4. Better display for "thinking models"

## Contributing
Pull requests are welcome!

## License
Nova is released under the MIT License – see [LICENSE](LICENSE) for details.

## Acknowledgements
- [Django](https://www.djangoproject.com/) – the rock‑solid web framework
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