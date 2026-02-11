# AGENTS.md

This file defines local conventions for Codex agents working in this repository.

## Quick Context

- Main Django project: `nova`
- User settings app: `user_settings`
- Django entrypoint: `manage.py`
- Useful docs:
  - `README-dev.md` (project structure)
  - `README-agents.md` (functional agent setup)

## Python Environment

Always use the local virtual environment:

```bash
. .venv/bin/activate
```

## Running Django Tests

Use test settings and force stable environment variables:

```bash
DEBUG=False \
CSRF_TRUSTED_ORIGINS='https://localhost,https://testserver' \
DJANGO_SETTINGS_MODULE=nova.settings_test \
python manage.py test
```

Targeted test example:

```bash
DEBUG=False \
CSRF_TRUSTED_ORIGINS='https://localhost,https://testserver' \
DJANGO_SETTINGS_MODULE=nova.settings_test \
python manage.py test user_settings.tests.test_tasks_views
```

Why:

- `nova.settings_test` imports `nova.settings`.
- If `DEBUG=True` is loaded from `.env`, `debugpy.listen()` can fail.
- Forcing `DEBUG=False` and HTTPS origins avoids those issues during tests.

## Useful Commands

Quick Python syntax check (without running the full suite):

```bash
python -m py_compile user_settings/views/tasks.py
```

Fast code search:

```bash
rg "TaskDefinition|EMAIL_POLL|email_tool" nova user_settings
```

## Contribution Conventions

- Create branches with the `codex/` prefix.
- Prefer targeted tests around changed files.
- Do not edit vendored/minified files.
- Prefer non-blocking UI warnings when behavior should remain allowed.
