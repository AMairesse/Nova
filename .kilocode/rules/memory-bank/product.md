# Product Overview

## What Nova Is

Nova is a **privacy-first personal AI workspace** that enables users to create and manage intelligent agents with access to various tools and capabilities. It's designed to give users complete control over their AI interactions while keeping sensitive data secure.

## Core Problems Nova Solves

1. **Privacy Control**: Users want to use AI without sending all their sensitive data to external services
2. **Model Flexibility**: Need to switch between local and cloud models based on task sensitivity  
3. **Tool Integration**: AI agents need access to various tools (calendar, web search, code execution, file management)
4. **Agent Orchestration**: Complex tasks require multiple specialized agents working together
5. **Multi-tenancy**: Each user needs isolated agents, tools, and data

## How It Works

### User Journey

1. **Setup**: User creates account, configures LLM providers (OpenAI, Mistral, Ollama, llama.cpp, LM Studio)
2. **Agent Creation**: User creates agents with custom system prompts and assigned tools
3. **Tool Assignment**: Agents can use built-in tools, MCP servers, REST APIs, or other agents as tools
4. **Interaction**: User chats with agents through threads (conversations)
5. **Execution**: Agent processes requests, calls tools, delegates to sub-agents as needed
6. **Real-time Updates**: User sees progress via WebSocket streaming (tool calls, sub-agent invocations)

### Key User Workflows

**Creating an Agent**:
- Select LLM provider (determines which model processes requests)
- Write system prompt (defines agent behavior and expertise)
- Assign tools (calendar, web search, file tools, code execution, memory)
- Optionally mark as "tool" so other agents can use it
- Set recursion limit (prevents infinite loops)

**Using Agents**:
- Create new thread or select existing conversation
- Send message to agent
- Watch real-time streaming of agent reasoning and tool calls
- Receive final answer
- Agent can ask clarifying questions (interactions) during processing

**Tool Management**:
- Built-in tools: Pre-configured utilities (CalDAV, web search, file management, date/time, memory, code execution, webapps)
- MCP tools: Connect to Model Context Protocol servers for external capabilities
- API tools: Integrate custom REST endpoints
- Agent tools: Use other agents as specialized sub-agents

## Expected User Experience

### Privacy-First
- Sensitive tasks use local models (Ollama, llama.cpp)
- Public tasks can use cloud models (OpenAI, Mistral)
- All API keys encrypted at rest
- Multi-tenant isolation ensures data never leaks between users

### Flexible & Extensible
- Switch models per agent without changing prompts
- Add new tools without code changes (via MCP)
- Chain agents for complex workflows
- File support for document processing

### Real-Time Feedback
- See agent thinking process
- Watch tool invocations as they happen
- Track progress of long-running tasks
- Get immediate feedback on errors

### Simple Yet Powerful
- Web UI for all configuration (no coding required)
- Django backend handles orchestration
- Celery for async task processing
- WebSocket for real-time updates
- Docker deployment for easy setup

## Success Metrics

Nova succeeds when users can:
- Set up privacy-preserving AI workflows in minutes
- Trust their sensitive data stays local
- Build complex multi-agent systems through UI alone
- Get consistent, reliable results from agents
- Scale from simple queries to production workflows