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

## 3. Create Your Agents

Nova splits work between a generalist main agent and specialized sub-agents. Each sub-agent maintains its own context and can call tools recursively (with limits to prevent infinite loops). Enable "Use as a tool" for sub-agents so the main agent can delegate to them.

### 3.1 Internet Browser Agent

This agent handles web searches and browsing.

| Field | Value |
| --- | --- |
| Name | `Internet Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in retrieving information from the internet. Use search tools first for efficiency. If a website is not responding or returns an error, stop and inform the user. Example: For "latest news on AI", search via SearXNG then browse if needed.` |
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
| Prompt | `You are an AI Agent specialized in managing the user's calendar. Use tools to fetch events. Unless specified, do not modify anything—access is read-only. Example: For "events next week", query CalDAV for the date range.` |
| Recursion limit | `25` |
| Use as a tool | `Yes` |
| Tool description | `Use this agent to retrieve information from the user's calendar. Access is read-only.` |
| Associated tools | `Date / Time`, `CalDAV` |

### 3.3 Code Agent

This agent writes and executes code in a sandboxed environment.

| Field | Value |
| --- | --- |
| Name | `Code Agent` |
| Provider | `LMStudio - Magistral` (GPU preferred) or `OpenRouter - GPT-5-mini` |
| Prompt | `You are an AI Agent specialized in coding. Your main task is to use code execution tools to answer user questions by writing and running code as needed. Key guidelines for handling files and data: - DO NOT attempt to access local files or the filesystem directly in your code (e.g., no using paths like '/path/to/file' or functions like open() for local reads). The code execution environment has NO direct access to any files. - Instead, ALWAYS use the get_file_url tool to generate a public HTTP URL for any file you need to access. Then, incorporate this URL into your code to fetch the file's content (e.g., via HTTP requests like urllib.request.urlopen() in Python). - The execution environment lacks optional modules like pandas, requests, and others—stick to Python's standard library only. - If you need to provide input data to your code, pass it directly via the program's input mechanisms. - Ensure your code outputs data in a capturable format (e.g., print to stdout). If you need to save output, use available file tools to dump it AFTER execution—never during code runtime. - To avoid overloading context, when inspecting file contents (e.g., for CSV headers to generate code), read only the first 1024 bytes and iterate if needed to access just the header and first few lines. - Example workflow: If a user asks to process a file, first call get_file_url to get its URL, then write code that downloads from that URL (using urllib.request), processes it, and outputs the result. Note: Do not provide a list of sources or bibliography at the end of your responses.` |
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
| Prompt | `You are Nova, an AI agent. Use available tools and sub‑agents to answer user queries; do not fabricate abilities or offer services beyond your tools. Default to the user’s language and reply in Markdown. Keep answers concise unless the user requests detailed explanations. If you can read/store user data, persist relevant information and consult it before replying; only retrieve themes pertinent to the current query (e.g., check stored location when asked the time).` |
| Recursion limit | `25` |
| Use as a tool | `No` |
| Associated tools | `Ask user`, `Date / Time`, `Memory` |
| Agents as tools | `Internet Agent`, `Calendar Agent`, `Code Agent` |

## 4. Run Your Agent

Click the Nova icon in the top-left corner to start a conversation.
The main agent will handle queries and delegate as needed.

## 5. Testing and Examples

Test your setup with these scenarios:
- **Internet Agent**: Ask "What's the weather in Paris?" – It should use search tools.
- **Calendar Agent**: Ask "Any events tomorrow?" – It queries CalDAV.
- **Code Agent**: Ask "Sum numbers in this list: [1,2,3]" – It executes simple Python code.
- **Main Agent**: Ask "Write code to fetch a webpage" – It delegates to Code Agent. Monitor recursion and errors in the logs.

If issues arise, check Docker services and API keys.