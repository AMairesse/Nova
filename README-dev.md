# Nova - Development documentation

## Project layout

```
Nova
├─ docker/                              # Docker compose configuration for the project (see `docker/README.md`)
├─ locale/                              # Project's translations files
|  ├─ en/LC_MESSAGES/django.po          # Django translations file
|  └─ en/LC_MESSAGES/djangojs.po        # Django JavaScript translations file
├─ nova/
|  ├─ api/                              # Minimal REST facade
|  |  ├─ serializers.py                 # Django REST serializers
|  |  ├─ urls.py                        # Django REST URLs
|  |  └─ views.py                       # Django REST views
|  ├─ llm/                              # LLM integration
|  |  ├─ checkpoints.py                 # LLM's checkpoints management functions
|  |  ├─ llm_agent.py                   # Base model for an LLM agent
|  |  └─ llm_tools.py                   # Tools management functions for the LLM agent
|  ├─ mcp/                              # Thin wrapper around FastMCP
|  |  └─ client.py                      # MCPClient class
|  ├─ migrations/                       # Django model migration scripts
|  ├─ models/                           # Django models
|  |  ├─ AgentConfig.py                 # AgentConfig object's model
|  |  ├─ CheckpointLink.py              # CheckpointLink object's model
|  |  ├─ Interaction.py                 # Interaction and InteractionStatus objects' model
|  |  ├─ Message.py                     # Actor, Message, MessageType objects' model
|  |  ├─ Provider.py                    # ProviderType and LLMProvider objects' model
|  |  ├─ Task.py                        # Task and TaskStatus objects' model
|  |  ├─ Thread.py                      # Thread object's model
|  |  ├─ Tool.py                        # Tool and ToolCredential objects' model
|  |  ├─ UserFile.py                    # UserFile object's model
|  |  └─ UserObjects.py                 # UserInfo, UserParameters and UserProfile objects' model
|  ├─ static/                           # JS helpers (streaming, tool modal manager…)
|  |  ├─ css/                           # CSS helpers
|  |  |  └─ main.css                    # CSS helpers
|  |  ├─ images/                        # Images
|  |  ├─ js/                            # JS helpers
|  |  |  ├─ files.js                    # Files' panel helper
|  |  |  ├─ responsive.js               # Bootstrap-native responsive behavior
|  |  |  ├─ thread-manager.js           # Unified thread module (bootstrap + pagination + DOM grouping)
|  |  |  └─ utils.js                    # VariousJS helpers
|  |  ├─ favicon.ico                    # Favicon
|  |  ├─ manifest.json                  # PWA manifest
|  |  └─ sw.js                          # Service Worker
|  ├─ templates/                        # Django + Bootstrap 5 UI
|  ├─ tests/                            # Django tests
|  ├─ tools/                            # Built‑in tool modules (CalDav, agent wrapper…)
|  ├─ views/                            # Django views
|  ├─ admin.py                          # Django admin
|  ├─ apps.py                           # Django apps
|  ├─ asgi.py                           # Django ASGI
|  ├─ celery.py                         # Django Celery
|  ├─ consumer.py                       # 
|  ├─ context_processors.py             # Django context processors
|  ├─ file_utils.py                     # File utilities
|  ├─ routing.py                        # Django routing
|  ├─ settings_test.py                  # Django test settings
|  ├─ settings.py                       # Django settings
|  ├─ signals.py                        # Django signals
|  ├─ tasks.py                          # Django tasks
|  ├─ urls.py                           # Django URLs
|  ├─ utils.py                          # Django utils
|  └─ wsgi.py                           # Django WSGI
├─ screenshots/                         # Screenshots
├─ user_settings/                       # Dedicated Django app for the user settings (see `user_settings/README.md`)
├─ .coveragerc                          # Coverage configuration file
├─ .flake8                              # Flake8 configuration file
├─ .gitignore                           # Git ignore file
├─ LICENCE                              # Licence file
├─ manage.py                            # Django management script
├─ README-agents.md                     # Agents documentation
├─ README-dev.md                        # This file : Development documentation
├─ README.md                            # Nova's general documentation
└─ requirements.txt                     # Project dependencies
```

## Data model

### Conversations between users and agents

- The conversation between a user and one or multiple agent is a ```Thread``` object.
- During a conversation the user can choose to switch to another agent for each new message sent.
- A Thread contains multiple ```Messages```.


## Workflow for specific use cases

### An agent asks the user for input

