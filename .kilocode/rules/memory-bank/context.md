# Current Context

## Current Work Focus

Memory bank initialization completed and verified by user. All core memory bank files are accurate and ready for use.

## Recent Changes

**2025-11-02:**
- Initialized memory bank with comprehensive project documentation
- Created `product.md` - Product vision and user experience
- Created `architecture.md` - System architecture and design patterns
- Created `tech.md` - Technology stack and development setup
- Created `context.md` - Current project state
- User verified memory bank accuracy
- User corrected test command to use `--settings nova.settings_test`

## Next Steps

1. Memory bank is now the foundation for all future development work
2. Any new features or changes will be documented here
3. Context will be updated as work progresses

## Key Findings from Analysis

**Project Type:** Django-based multi-tenant AI agent platform

**Core Technologies:**
- Django 5.2 + Django Channels for WebSocket
- Celery + Redis for async processing
- PostgreSQL for data storage  
- MinIO for file storage
- LangChain/LangGraph for agent orchestration
- FastMCP for tool integration

**Key Features:**
- Multi-tenant architecture with user isolation
- Real-time agent execution via WebSocket
- Support for multiple LLM providers (local and cloud)
- Extensible tool system (built-in, MCP, REST, agent-as-tool)
- File attachment support with MinIO
- Agent checkpoint management for state persistence

**Architecture Patterns:**
- Factory pattern for LLM provider abstraction
- Plugin architecture for tool system
- Composite pattern for agent-as-tool
- Pub/Sub for real-time updates
- Message queue for async processing

## Important Notes

- All models use user foreign key for multi-tenancy
- API keys encrypted at rest with Fernet
- File paths sanitized for security
- Agent recursion limited to prevent infinite loops
- WebSocket channels for real-time progress updates
- Docker Compose for deployment with optional services

## Testing Guidelines

- Unit tests run with: `python manage.py test --settings nova.settings_test`
- **Do not launch the application** - user handles all app testing
- Focus on code analysis, planning, and documentation