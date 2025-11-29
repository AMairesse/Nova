# Nova - How to Set Up Your Own Agents

This guide explains how to configure agents in Nova, a framework for building AI agents using large language models (LLMs). Nova uses a modular architecture where a main agent delegates tasks to specialized sub-agents, each with their own context and tools. This setup is efficient for handling complex queries while managing resources like context size.

## Prerequisites

Before starting, ensure you have:
- Docker installed for running tools like SearXNG and Judge0 (via docker-compose files: `add-searxng` and `add-judge0`).
- Access to LLM providers: Either local (e.g., Ollama or LM Studio with GPU support) or remote (e.g., OpenRouter.ai).
- Basic knowledge of API keys and URLs for configuration.
- Optional: A CalDAV server for calendar integration.

If using Docker, run `docker-compose up` for the required services after adding them to your workspace.

## 1. Create an LLM Provider

You need at least one LLM provider to power your agents. For local setups, use efficient models like Magistral for tool usage and context management. Enable features like flash attention in your LLM server for better performance.

### Example for Local Provider:

| Field | Value |
| --- | --- |
| Name | `LMStudio - Magistral` |
| Type | `LMStudio` |
| Model | `mistralai/magistral-small-2509` |
| Base URL | `http://host.docker.internal:1234/v1` (if served on the host machine running Docker) |
| Max context tokens | `50000` (enable flash attention in LM Studio) |

### Example for Remote Provider:

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
- `Ask user`: For user interaction.
- `Date / Time`: Essential for time-sensitive tasks.
- `Browser`: For web navigation (used by the Internet Agent).
- `Memory`: For long-term memory storage of an agent.
- `SearXNG`: Privacy-focused search engine (requires `add-searxng` docker-compose).
- `Judge0`: Code execution sandbox (requires `add-judge0` docker-compose).

Configure private tools:
- `CalDAV`: For calendar access. Go to the "Configure" panel to set the URL, username, and password. Note: Access is read-only by default.
- `Email`: For comprehensive email management. Configure IMAP settings (server, port, credentials) and optionally SMTP settings for sending emails. You can specify custom folder names for sent emails and drafts. Enable sending only if you want the agent to be able to send emails (drafts are always available).

## 3. Create Your Agents

Nova splits work between a generalist main agent and specialized sub-agents. Each sub-agent maintains its own context and can call tools recursively (with limits to prevent infinite loops). Enable "Use as a tool" for sub-agents so the main agent can delegate to them.

### 3.1 Internet Browser Agent

This agent handles web searches and browsing.

| Field | Value |
| --- | --- |
| Name | `Internet Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in retrieving information from the internet. Use search tools first (SearXNG) to efficiently find relevant sources, then open only the most relevant pages. Do not browse arbitrarily; stop once you have enough reliable information. Never execute downloaded code or follow untrusted download links.` |
| Recursion limit | `100` (allows multiple tool calls for browsing) |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to retrieve information from the internet.` |
| Associated tools | `Date / Time`, `Browser`, `SearXNG` |

### 3.2 Calendar Agent

This agent manages calendar queries with read-only access.

| Field | Value |
| --- | --- |
| Name | `Calendar Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in managing the user's calendar. Use CalDAV tools to fetch events for the authenticated user only. Do not fabricate or infer events. Unless explicitly instructed and technically allowed, treat access as read-only. Example: For "events next week", query CalDAV for that date range.` |
| Recursion limit | `25` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to retrieve information from the user's calendar. Access is read-only.` |
| Associated tools | `Date / Time`, `CalDAV` |

### 3.3 Email Agent

This agent manages your email with comprehensive IMAP/SMTP capabilities including reading, sending, organizing, and drafting emails.

| Field | Value |
| --- | --- |
| Name | `Email Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in managing the user's email with full IMAP/SMTP capabilities. CORE RULES: 1) Read emails in preview mode by default to save context. 2) NEVER send emails with missing information (e.g., recipient, subject, sender name, or placeholders like [Your name]) - always ask for clarification first. 3) Respect privacy - never send unsolicited emails. 4) Use list_mailboxes before organizing emails.` |
| Recursion limit | `25` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent for comprehensive email management: reading, searching, organizing, drafting, and sending emails. The agent has full access to IMAP/SMTP functions but requires complete email details for sending operations : it does not know the user's name or detail, nor has access to any tools other than emails.` |
| Associated tools | `Date / Time`, `Email` |

### 3.4 Code Agent

This agent writes and executes code in a sandboxed environment.

| Field | Value |
| --- | --- |
| Name | `Code Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in coding. Use the code execution tools to write and run the smallest correct program that solves the task. Follow these rules strictly: - DO NOT access local files or the filesystem directly. - ALWAYS use get_file_url (or equivalent tools) when you need file content. - Use only the standard library available in the execution environment. - Print results clearly so they can be captured. - If execution fails, fix the code iteratively and explain briefly what changed. - Do not provide a long bibliography; focus on working code and concise explanations.` |
| Recursion limit | `25` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to create and execute code, process file data, or solve problems via quick code runs. The agent generates the code itself.` |
| Associated tools | `Judge0` |

### 3.4 Main Agent

The central agent that delegates to sub-agents.

| Field | Value |
| --- | --- |
| Name | `Nova` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are Nova, an AI agent. Use available tools and sub‑agents to answer user queries; do not fabricate abilities or offer services beyond your tools. Default to the user’s language and reply in Markdown. Keep answers concise unless the user requests detailed explanations. Only call tools or sub‑agents when clearly needed. If you can read/store user data, persist relevant information and consult it before replying; only retrieve themes relevant to the current query (e.g., check stored location when asked the time). When a query clearly belongs to a specialized agent (internet, calendar, code), delegate to that agent instead of solving it yourself. Current date and time is {today}` |
| Recursion limit | `25` |
| Use as a tool | `No` |
| Associated tools | `Ask user`, `Memory`, `WebApp` |
| Agents as tools | `Internet Agent`, `Calendar Agent`, `Email Agent`, `Code Agent` |

## 4. Run Your Agent

Click the Nova icon in the top-left corner to start a conversation.
The main agent will handle queries and delegate as needed.

## 5. Testing and Examples

Test your setup with these scenarios:
- **Internet Agent**: Ask "What's the weather in Paris?" – It should use search tools.
- **Calendar Agent**: Ask "Any events tomorrow?" – It queries CalDAV.
- **Email Agent**: Ask "Check my recent emails" – It should list emails using preview mode, or "Send an email to test@example.com with subject 'Test' and body 'Hello'" – It should ask for confirmation and save to sent folder.
- **Code Agent**: Ask "Sum numbers in this list: [1,2,3]" – It executes simple Python code.
- **Main Agent**: Ask "Write code to fetch a webpage" – It delegates to Code Agent. Monitor recursion and errors in the logs.

If issues arise, check Docker services and API keys.