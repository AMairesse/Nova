# Nova - How to Set Up Your Own Agents

This guide explains how to configure agents in Nova.
Nova now uses a hybrid model:
- a main agent (`Nova`) with direct tools and on-demand skills
- a small set of specialized sub-agents for focused domains (internet, code)

Mail and Calendar are no longer configured as dedicated sub-agents in the default setup.

## Prerequisites

Before starting, ensure you have:
- Docker installed for running tools like SearXNG and Judge0 (via docker-compose files: `add-searxng` and `add-judge0`).
- Access to LLM providers: local (for example Ollama or LM Studio) or remote (for example OpenRouter.ai).
- Basic knowledge of API keys and URLs for configuration.
- Optional: a CalDAV server and one or more email accounts.

If using Docker, run `docker-compose up` for required services after adding them to your workspace.

## 1. Create an LLM Provider

You need at least one LLM provider to power your agents.

### Example for Local Provider

| Field | Value |
| --- | --- |
| Name | `LMStudio - Magistral` |
| Type | `LMStudio` |
| Model | `mistralai/magistral-small-2509` |
| Base URL | `http://host.docker.internal:1234/v1` |
| Max context tokens | `50000` |

### Example for Remote Provider

| Field | Value |
| --- | --- |
| Name | `OpenRouter - GPT-5-mini` |
| Type | `OpenAI` |
| Model | `openai/gpt-5-mini` |
| API key | `Enter your API key` |
| Base URL | `https://openrouter.ai/api/v1` |
| Max context tokens | `400000` |

## 2. Create Your Tools

Add these default tools to your Nova workspace:
- `Ask user`
- `Date / Time`
- `Browser`
- `Memory`
- `WebApp`
- `SearXNG` (requires `add-searxng`)
- `Judge0` (requires `add-judge0`)

Configure private tools:
- `CalDAV`: set URL, username, password.
- `Email`: set IMAP settings (required), and SMTP settings if sending is enabled.

You can configure multiple Email and CalDAV tools for the same user. Nova will aggregate them under skills at runtime.

## 3. Create Your Agents

Nova defaults to:
- one main agent: `Nova`
- two sub-agents used as tools: `Internet Agent`, `Code Agent`

Do not create dedicated `Calendar Agent` or `Email Agent` in the default model.

### 3.1 Internet Agent

| Field | Value |
| --- | --- |
| Name | `Internet Agent` |
| Provider | `LMStudio - Magistral` or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in retrieving information from the internet. Use search tools first (SearXNG) to efficiently find relevant sources, then open only the most relevant pages with the browser. Do not browse arbitrarily; stop once you have enough reliable information. Never execute downloaded code or follow untrusted download links. If a website is not responding or returns an error, stop and inform the user.` |
| Recursion limit | `100` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to retrieve information from the internet.` |
| Associated tools | `Date / Time`, `Browser`, `SearXNG` |

### 3.2 Code Agent

| Field | Value |
| --- | --- |
| Name | `Code Agent` |
| Provider | `LMStudio - Magistral` or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in coding. Use the code execution tools to write and run the smallest correct program that solves the task. Follow these rules strictly: DO NOT access local files or the filesystem directly; ALWAYS use provided file-url tools when you need file content; use only the standard library available in the execution environment; print results clearly so they can be captured; if execution fails, fix the code iteratively and briefly explain what changed; focus on working code and concise explanations.` |
| Recursion limit | `25` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to create and execute code or process data using sandboxed runtimes.` |
| Associated tools | `Judge0` |

### 3.3 Main Agent (Nova)

| Field | Value |
| --- | --- |
| Name | `Nova` |
| Provider | `LMStudio - Magistral` or `OpenRouter - GPT-5-mini` |
| Prompt | `You are Nova, an AI agent. Use available tools and sub‑agents to answer user queries; do not fabricate abilities or offer services beyond your tools. Default to the user’s language and reply in Markdown. Only call tools or sub‑agents when clearly needed. If you can read/store user data, persist relevant information and consult it before replying; only retrieve themes relevant to the current query (e.g., check stored location when asked the time). When a query clearly belongs to a specialized agent (internet, code), delegate to that agent instead of solving it yourself. Use skills/tools directly for mail and calendar tasks. Current date and time is {today}` |
| Recursion limit | `25` |
| Use as a tool | `No` |
| Associated tools | `Ask user`, `Memory`, `Date / Time`, `WebApp`, `Email` (1..n), `CalDAV` (1..n) |
| Agents as tools | `Internet Agent`, `Code Agent` |

## 4. Skills Runtime Behavior

Mail and Calendar are exposed as on-demand skills in tool-based agent runtime:
- They are not always visible by default in model context.
- `Nova` can activate them on demand (for example via `load_skill("mail")` or `load_skill("caldav")`).
- With multiple configured instances, Nova uses aggregated tools and a selector argument (for example mailbox/account identifier).

## 5. Run Your Agent

Click the Nova icon in the top-left corner to start a conversation.

## 6. Testing and Examples

Test your setup with these scenarios:
- **Internet**: "What's the weather in Paris?" (should delegate to `Internet Agent`).
- **Mail skill**: "Check my recent emails." (should activate Mail skill and use mail tools).
- **Calendar skill**: "Any events next week?" (should activate CalDAV skill and use calendar tools).
- **Code**: "Sum numbers in this list: [1,2,3]." (should delegate to `Code Agent`).
- **Main orchestration**: "Read my last email then draft a reply." (mail workflow through skills, no Email sub-agent required).

## Migration Note

Default bootstrap automatically detaches legacy `Calendar Agent` and `Email Agent` links from `Nova`.
These legacy agent rows are not deleted from the database.
