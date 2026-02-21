# Architecture Overview

## System Architecture

Nova is a **Django-based multi-tenant AI agent platform** with real-time capabilities powered by Django Channels (WebSockets), async task processing via Celery, and Docker-based deployment.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         User Browser                         │
│        (WebSocket for real-time + HTTP for REST)            │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Nginx (Port 8080)                       │
│              Static Files + Reverse Proxy                    │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Daphne (ASGI Server)                      │
│         HTTP + WebSocket handling (Port 8000)                │
└───┬────────────────────────────────────────────────┬─────────┘
    │                                                 │
    ▼                                                 ▼
┌───────────────────────┐              ┌─────────────────────┐
│   Django Application  │              │  Channels/WebSocket │
│   (HTTP endpoints)    │              │   (Real-time)       │
└───────┬───────────────┘              └─────────┬───────────┘
        │                                        │
        │                                        │
        ▼                                        ▼
┌──────────────────────────────────────────────────────────┐
│                   Redis (Channel Layer)                   │
│         WebSocket message broker + Celery broker          │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│                    Celery Workers                         │
│         Async agent execution + tool calling              │
└───┬──────────────────────────────────────────────────┬───┘
    │                                                   │
    ▼                                                   ▼
┌───────────────────┐                    ┌────────────────────┐
│   PostgreSQL      │                    │   MinIO (S3)       │
│   (Main DB)       │                    │   (File Storage)   │
└───────────────────┘                    └────────────────────┘
```

## Skill Middleware (Tool-Based Agents)

Nova introduces a runtime skill layer for builtins:

1. Builtin modules declare loading policy in `METADATA.loading`.
2. Agent runtime exposes control tools (`list_skills`, `load_skill`).
3. A model-call middleware filters tool visibility dynamically:
- skill tools hidden by default
- skill tools visible after `load_skill(...)` for current turn only
4. Skill instructions are injected only after activation.
5. Existing builtin aggregation (notably email multi-mailbox) remains unchanged.

## Core Components

### 1. Django Applications

**Main Application: `nova/`**
- Entry point and core logic
- Models, views, URL routing
- Agent orchestration
- Real-time WebSocket consumers

**User Settings: `user_settings/`**
- Provider configuration
- Agent management
- Tool management
- User preferences

### 2. Key Modules

**Models (`nova/models/`):**
- [`Thread.py`](nova/models/Thread.py) - Conversation threads
- [`Message.py`](nova/models/Message.py) - Messages with actor types (User/Agent/System)
- [`AgentConfig.py`](nova/models/AgentConfig.py) - Agent definitions
- [`Provider.py`](nova/models/Provider.py) - LLM provider configurations
- [`Tool.py`](nova/models/Tool.py) - Tool definitions and credentials
- [`UserFile.py`](nova/models/UserFile.py) - File attachments stored in MinIO
- [`Interaction.py`](nova/models/Interaction.py) - Agent-to-user questions
- [`Task.py`](nova/models/Task.py) - Async task tracking
- [`CheckpointLink.py`](nova/models/CheckpointLink.py) - LangGraph checkpoint references

**LLM Layer (`nova/llm/`):**
- Agent orchestration using LangGraph
- Provider integration (OpenAI, Mistral, Ollama, llama.cpp, LM Studio)
- Tool binding and execution
- Checkpoint management for state persistence

**MCP Client (`nova/mcp/`):**
- [`client.py`](nova/mcp/client.py) - FastMCP client wrapper
- Caches tool discovery
- Handles authentication (Bearer, OAuth, etc.)
- Async-first API

**Tools (`nova/tools/`):**
- [`builtins/`](nova/tools/builtins/) - Built-in tool implementations
  - Calendar (CalDAV)
  - Web search (SearXNG)
  - File management
  - Date/time utilities
  - Memory (user preferences)
  - Code execution (Judge0)
  - Webapps
- [`agent_tool_wrapper.py`](nova/tools/agent_tool_wrapper.py) - Wraps agents as tools
- [`files.py`](nova/tools/files.py) - File-related tool functions

**Views (`nova/views/`):**
- Thread management
- Message handling
- File upload/download
- Task status
- Interaction handling

**Tasks (`nova/tasks/`):**
- [`agent_tasks.py`](nova/tasks/agent_tasks.py) - Celery tasks for agent execution
- Async agent invocation
- Real-time progress broadcasting

**API (`nova/api/`):**
- REST endpoints for programmatic access
- Token-based authentication

### 3. Real-Time Architecture

**WebSocket Flow:**
```
User sends message
    ↓
View creates Celery task
    ↓
Task executes in background
    ↓
Agent processes (LangGraph)
    ↓
Progress updates sent to Channel Layer (Redis)
    ↓
WebSocket consumer broadcasts to client
    ↓
UI updates in real-time
```

**WebSocket Consumers ([`nova/consumers.py`](nova/consumers.py)):**
- `TaskProgressConsumer` - Agent execution progress
- `FileProgressConsumer` - File upload progress

### 4. Data Flow

**Agent Execution Flow:**
```
User Message → Thread → Celery Task → LLMAgent.create()
                                           ↓
                                    Load user params
                                           ↓
                                    Fetch agent data (provider, tools, sub-agents)
                                           ↓
                                    Create LangChain LLM
                                           ↓
                                    Load tools (builtin, MCP, API, agent tools)
                                           ↓
                                    Create LangGraph agent (create_agent)
                                           ↓
                                    Execute with checkpoint
                                           ↓
                                    Tool calls → MCP/Builtin/SubAgent
                                           ↓
                                    Stream progress via WebSocket
                                           ↓
                                    Save response → Message
```

**Tool Execution Flow:**
```
Agent needs tool → LangChain tool call
                        ↓
                   Tool type check
                        ↓
    ┌──────────────────┼──────────────────┐
    │                  │                  │
    ▼                  ▼                  ▼
Builtin          MCP Server        Agent Tool
Python func      HTTP request      Recursive invoke
Direct exec      FastMCP client    New LLMAgent
    │                  │                  │
    └──────────────────┴──────────────────┘
                        ↓
                   Return result
                        ↓
                   Continue agent
```

**Manual Summarization Flow:**
```
User clicks "Compact" → Check main agent message count
                              ↓
                       Check sub-agents with context
                              ↓
    ┌──────────────────┼──────────────────┐
    │                  │                  │
    ▼                  ▼                  ▼
No sub-agents    Sub-agents found    Insufficient context
with context     → CONFIRMATION_NEEDED   → Direct summarization
    │                  ↓                  │
    └─────────────── User chooses ───────┘
                              ↓
    ┌──────────────────┼──────────────────┐
    │                  │                  │
    ▼                  ▼                  ▼
Main agent only   Main + sub-agents   Celery task created
    ↓                  ↓                  ↓
Sequential       Sequential processing  Progress via WebSocket
summarization     with error handling    ↓
    ↓                  ↓               UI updates
    └──────────────────┴──────────────────┘
                        ↓
               Checkpoint injection
                        ↓
               Context optimization complete
```

## Critical Implementation Paths

### 1. Agent Creation and Invocation

**Entry Point:** [`nova/tasks/agent_tasks.py`](nova/tasks/agent_tasks.py) → `run_agent_task()`

**Key Steps:**
1. Fetch thread and agent config
2. Create LLMAgent instance via `LLMAgent.create()`
3. Build system prompt (with template variables like `{today}`)
4. Load and configure tools
5. Create LangGraph checkpoint for state persistence
6. Execute agent with streaming
7. Broadcast progress via WebSocket
8. Save final response

### 2. Tool Loading

**Location:** `nova/llm/llm_tools.py` → `load_tools()`

**Process:**
1. **Built-in tools:** Import Python modules dynamically, call `get_functions()`
2. **MCP tools:** Create MCPClient, fetch tool list, wrap as LangChain tools
3. **Agent tools:** Wrap sub-agents using `AgentToolWrapper`
4. **File tools:** Always included for file operations
5. Return unified list of LangChain-compatible tools

### 3. Multi-Tenancy

**Implementation:**
- All models filtered by `user` foreign key
- Encryption keys per-deployment (FIELD_ENCRYPTION_KEY)
- API keys encrypted using `django-encrypted-model-fields`
- Token-based API authentication
- Row-level security via Django ORM filters

### 4. File Management

**Storage:** MinIO (S3-compatible)

**Path Structure:**
```
users/{user_id}/threads/{thread_id}/{user_path}
```

**Operations:**
- Upload: `file_utils.py` → `upload_file_to_minio()` (async, multipart for large files)
- Download: Pre-signed URLs via MinIO client
- Context: Files auto-included in agent context
- Cleanup: Signals delete from MinIO when thread deleted (via Django signals)

### 5. Checkpoint Management

**Purpose:** State persistence for LangGraph agents

**Storage:** PostgreSQL via `langgraph-checkpoint-postgres`

**Linking:** `CheckpointLink` model maps `thread + agent → checkpoint_id`

**Lifecycle:**
- Created on first agent invocation
- Updated on each tool call
- Deleted when thread deleted (via Django signals)

## Design Patterns

### 1. Multi-Provider Abstraction

**Pattern:** Factory + Strategy

**Implementation:**
```python
# LLMAgent.create_llm_agent() in nova/llm/llm_agent.py
if provider_type == ProviderType.OPENAI:
    return ChatOpenAI(...)
elif provider_type == ProviderType.MISTRAL:
    return ChatMistralAI(...)
# etc.
```

### 2. Tool Plugin System

**Pattern:** Plugin Architecture

**Registration:** `nova/tools/__init__.py` maintains registry of available tool types

**Extension:** Add new built-in by creating module in `nova/tools/builtins/` with `get_functions()` method

### 3. Agent-as-Tool

**Pattern:** Composite

**Implementation:** `AgentToolWrapper` makes agents callable as LangChain tools, enabling recursive agent delegation with depth limits

### 4. Async Task Processing

**Pattern:** Message Queue (Celery)

**Why:** Long-running agent executions don't block web requests, enables real-time progress updates

### 5. Real-Time Updates

**Pattern:** Pub/Sub (Django Channels)

**Flow:** Celery task → Redis channel → WebSocket consumer → Client

## Security Considerations

1. **Encryption:** API keys encrypted at rest using Fernet (FIELD_ENCRYPTION_KEY)
2. **Isolation:** Multi-tenant via user foreign keys, no cross-user data access
3. **Authentication:** Django session auth for web, token auth for API
4. **CSRF Protection:** Django CSRF middleware, trusted origins configured
5. **Input Validation:** Django forms + model validation
6. **SQL Injection:** Protected by Django ORM
7. **File Upload:** MIME type validation, size limits (10MB), path sanitization
8. **Secrets Management:** Environment variables, not committed to repo

## Scalability Considerations

1. **Horizontal Scaling:** Celery workers can be scaled independently
2. **Caching:** Redis for channel layer, MCP tool discovery cached (5min)
3. **Database:** PostgreSQL connection pooling, indexed foreign keys
4. **File Storage:** MinIO supports distributed object storage
5. **Static Files:** Nginx serves static/media files directly
6. **WebSocket:** Each WebSocket connection handled by Daphne, scalable via load balancer

## Deployment Architecture

**Docker Compose Services:**
- `db` - PostgreSQL 16
- `redis` - Redis (channel layer + Celery broker)
- `minio` - MinIO object storage
- `web` - Django/Daphne (main app)
- `celery-worker` - Background task execution
- `nginx` - Reverse proxy + static files

**Optional Services:**
- `ollama` - Local LLM inference
- `llama.cpp` - Alternative local LLM
- `searxng` - Privacy-focused search engine
- `judge0` - Code execution sandbox

## Technology Stack Summary

- **Backend:** Django 5.2, Django REST Framework
- **ASGI Server:** Daphne (for WebSocket support)
- **Task Queue:** Celery 5.5 with Redis broker
- **Database:** PostgreSQL 16
- **File Storage:** MinIO (S3-compatible)
- **Real-time:** Django Channels 4.3 + channels_redis
- **LLM Framework:** LangChain + LangGraph
- **MCP Client:** FastMCP
- **Web Server:** Nginx
- **Orchestration:** Docker Compose
- **Frontend:** Bootstrap 5, vanilla JavaScript (WebSocket API)
