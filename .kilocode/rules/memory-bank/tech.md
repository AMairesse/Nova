# Technology Stack

## Core Technologies

### Backend Framework
- **Django 5.2.7** - Main web framework
- **Django REST Framework 3.16.1** - REST API framework
- **Django Channels 4.3.1** - WebSocket and async support
- **Daphne 4.2.1** - ASGI server for HTTP + WebSocket

### Database
- **PostgreSQL 16** - Primary database
- **psycopg2-binary 2.9.11** - PostgreSQL adapter
- **psycopg 3.2.11** - Async PostgreSQL adapter

### Task Queue & Caching
- **Celery 5.5.3** - Distributed task queue
- **Redis** - Message broker + channel layer
- **channels_redis 4.3.0** - Redis channel layer backend

### File Storage
- **MinIO** - S3-compatible object storage
- **aioboto3 15.4.0** - Async AWS SDK for Python
- **boto3 1.40.49** - AWS SDK for Python

### AI/ML Framework
- **LangChain 1.0.2** - LLM application framework
- **LangGraph 1.0.1** - Graph-based agent workflows
- **langgraph-checkpoint-postgres 3.0.0** - PostgreSQL checkpoint storage
- **LangSmith 0.4.38** - LLM observability (optional)

### LLM Providers
- **langchain-openai 1.0.1** - OpenAI integration
- **langchain-mistralai 1.0.1** - Mistral AI integration
- **langchain-ollama 1.0.0** - Ollama integration
- **openai 2.6.1** - OpenAI Python client
- **ollama 0.6.0** - Ollama Python client

### MCP (Model Context Protocol)
- **FastMCP 2.13.0** - MCP client implementation
- **mcp 1.19.0** - MCP protocol library

### Security & Encryption
- **django-encrypted-model-fields 0.6.5** - Field-level encryption
- **cryptography 46.0.3** - Cryptographic primitives
- **Authlib 1.6.5** - OAuth and authentication

### HTTP Client
- **httpx 0.28.1** - Async HTTP client
- **httpx-sse 0.4.3** - Server-sent events support
- **requests 2.32.5** - Synchronous HTTP client

### Web Automation
- **playwright 1.55.0** - Browser automation
- **beautifulsoup4 4.14.2** - HTML parsing

### Forms & UI
- **django-crispy-forms 2.4** - Form rendering
- **crispy-bootstrap5 2025.6** - Bootstrap 5 templates
- **Bootstrap 5** - Frontend framework (via CDN)

### Utilities
- **python-dotenv 1.1.1** - Environment variable management
- **caldav 2.0.1** - CalDAV client for calendar integration
- **python-magic 0.4.27** - MIME type detection
- **pathvalidate 3.3.1** - Path validation
- **whitenoise 6.11.0** - Static file serving

### Testing
- **coverage 7.11.0** - Code coverage measurement
- **Django TestCase** - Built-in test framework

## Development Setup

### Prerequisites
- Python 3.12
- Docker & Docker Compose
- Git

### Environment Variables

**Required:**
```env
# Database
DB_USER=postgres
DB_PASSWORD=<strong-password>

# Django
DJANGO_SECRET_KEY=<django-secret>
FIELD_ENCRYPTION_KEY=<fernet-key>

# MinIO
MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=<strong-password>
MINIO_ACCESS_KEY=nova_user
MINIO_SECRET_KEY=<strong-secret>

# Hosting
HOST_PORT=8080
ALLOWED_HOSTS=localhost
CSRF_TRUSTED_ORIGINS=http://localhost:8080
```

**Optional:**
```env
# Superuser (first run)
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=<strong-password>

# Ollama (local LLM)
OLLAMA_SERVER_URL=http://ollama:11434
OLLAMA_MODEL_NAME=llama3.2
OLLAMA_CONTEXT_LENGTH=4096

# llama.cpp (local LLM)
LLAMA_CPP_SERVER_URL=http://llama-cpp:8080
LLAMA_CPP_MODEL=qwen/qwen3-8B-GGUF
LLAMA_CPP_CTX_SIZE=4096

# SearXNG (web search)
SEARNGX_SERVER_URL=http://searxng:8080
SEARNGX_NUM_RESULTS=10

# Judge0 (code execution)
JUDGE0_SERVER_URL=http://judge0:2358

# Debug
DEBUG=True
```

### Docker Services

**Base Services:**
```yaml
services:
  db:           # PostgreSQL 16
  redis:        # Redis (channel layer + broker)
  minio:        # MinIO object storage
  nginx:        # Reverse proxy
  web:          # Django/Daphne
  celery-worker: # Background tasks
```

**Optional Add-ons:**
```yaml
# docker-compose.add-ollama.yml
ollama:         # Local LLM server

# docker-compose.add-llamacpp.yml
llama-cpp:      # Alternative local LLM

# docker-compose.add-searxng.yml
searxng:        # Privacy-focused search

# docker-compose.add-judge0.yml
judge0:         # Code execution sandbox
```

### Running Locally

**Production Mode:**
```bash
cd docker
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

**Development Mode:**
```bash
cd docker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

**With Optional Services:**
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.add-ollama.yml \
  -f docker-compose.add-searxng.yml \
  up -d
```

### Port Mappings

- `8080` - Nginx (main application)
- `8000` - Daphne (if running dev mode)
- `5432` - PostgreSQL (internal)
- `6379` - Redis (internal)
- `9000` - MinIO API (internal)
- `9001` - MinIO Console (internal)
- `11434` - Ollama (if enabled)

## Technical Constraints

### File Upload Limits
- **Max file size:** 10MB
- **Allowed MIME types:** 
  - Images: JPEG, PNG
  - Text: Plain, HTML, Markdown, CSV, Python
  - Documents: PDF, DOC
  - Data: JSON
- **Storage:** MinIO with multipart upload for files >5MB

### Context Window Management
- Configurable per provider (`max_context_tokens`)
- Default: 4096 tokens
- Large models: 100,000+ tokens
- File context automatically included in system prompt

### Agent Recursion
- Default limit: 25 iterations
- Configurable per agent
- Prevents infinite loops in agent delegation

### Multi-Tenancy
- All data isolated by user
- No cross-tenant access
- Encrypted API keys per user
- System-wide providers/tools available to all

### WebSocket Connections
- Ping/pong for keepalive
- Task-specific channels
- Automatic reconnection handling
- Real-time progress updates

### Async Processing
- All agent executions run in Celery
- Non-blocking UI
- Concurrent task support
- Task status tracking

## Code Organization

### Project Structure
```
Nova/
├── docker/                 # Docker config
│   ├── Dockerfile
│   ├── docker-compose*.yml
│   ├── nginx/             # Nginx config
│   ├── ollama/            # Ollama config
│   ├── searxng/           # SearXNG config
│   └── judge0/            # Judge0 config
├── nova/                  # Main Django app
│   ├── api/              # REST API
│   ├── llm/              # LLM integration
│   ├── mcp/              # MCP client
│   ├── migrations/       # DB migrations
│   ├── models/           # Data models
│   ├── static/           # CSS/JS
│   ├── tasks/            # Celery tasks
│   ├── templates/        # HTML templates
│   ├── tests/            # Test suite
│   ├── tools/            # Tool implementations
│   └── views/            # Django views
├── user_settings/        # User config app
│   ├── migrations/
│   ├── static/
│   ├── templates/
│   └── views/
├── locale/               # i18n translations
├── manage.py             # Django management
└── requirements.txt      # Python dependencies
```

### Key Conventions

**Models:**
- One model per file in `nova/models/`
- All inherit from `django.db.models.Model`
- User foreign key for multi-tenancy
- Encrypted fields for secrets

**Views:**
- Organized by feature in `nova/views/`
- Login required for most endpoints
- HTMX for partial page updates
- JSON responses for API

**Tasks:**
- Celery tasks in `nova/tasks/`
- Async execution with progress updates
- Error handling and retry logic

**Tools:**
- Built-in tools in `nova/tools/builtins/`
- Each tool exports `get_functions()`
- Async-first design
- Tool metadata in registry

**Tests:**
- Test files in `nova/tests/`
- Inherit from `BaseTestCase`
- Use Django TestCase for DB
- Mock external services

## Dependency Management

### Installing Dependencies
```bash
pip install -r requirements.txt
```

### Key Dependency Relationships
- Django Channels requires Daphne
- Celery requires Redis
- LangGraph requires LangChain
- File tools require python-magic
- Playwright requires system dependencies

### Optional Dependencies
- Langfuse for LLM observability
- debugpy for remote debugging (DEBUG=True)

## Development Tools

### Django Management Commands
```bash
python manage.py migrate                                # Run migrations
python manage.py createsuperuser                        # Create admin user
python manage.py collectstatic                          # Collect static files
python manage.py test --settings nova.settings_test     # Run tests
python manage.py shell                                  # Django shell
```

### Celery Commands
```bash
celery -A nova.celery worker -l info  # Start worker
celery -A nova.celery inspect active  # Check active tasks
```

### Database Migrations
```bash
python manage.py makemigrations      # Create migrations
python manage.py migrate             # Apply migrations
python manage.py showmigrations      # Show migration status
```

## Browser Compatibility

- Modern browsers with WebSocket support
- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- No IE support

## Performance Considerations

- Static files served by Nginx
- WhiteNoise for static file compression
- PostgreSQL connection pooling
- Redis for fast caching
- Async file uploads with chunking
- WebSocket for efficient real-time updates