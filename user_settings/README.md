# Nova - User Settings App

`user_settings/` is the Django app that configures the current Nova runtime for each user.

It owns the UI for:

- providers
- agents
- capabilities, connections, and credentials
- memory settings
- task definitions and templates
- API tokens and general preferences

## Project Layout

```text
user_settings/
├─ migrations/
├─ static/user_settings/js/
├─ templates/user_settings/
├─ views/
├─ forms.py
├─ mixins.py
├─ urls.py
└─ README.md
```

## Provider Settings

Provider configuration is split conceptually into:

- connection
- model
- capabilities

Key behaviors:

- `LLMProvider` stores both provider connection and selected model
- model discovery is provider-aware when supported
- metadata refresh imports declared capabilities
- active verification confirms real runtime behavior
- UI warnings consume `LLMProvider.capability_profile`

## Agent Settings

Agent forms let users select:

- one provider/model
- standard capabilities
- one backend choice for `Search` and `Python`
- attached connections
- delegated sub-agents
- summarization behavior

Important UX rules:

- agents warn when the selected provider/model is verified without tool support
- default bootstrap is role-aware
- the runtime choice is no longer exposed in settings

## Capabilities & Connections

The settings UI is organized around product concepts rather than a flat list of raw tools.

Built-in capabilities:

- `Date / Time`
- `Browser`
- `Memory`
- `WebApp`

These exist by default and are not user-created connections.

Capabilities with backends:

- `Search`
- `Python`

Current backend availability:

- `Search`
  - a deployment-default backend when SearXNG is enabled
  - one or more custom user backends
- `Python`
  - a deployment-default backend when `exec-runner` is enabled

Each agent selects at most one backend per family.

Connections:

- `Email`
- `Calendar`
- `WebDAV`
- `MCP`
- `API`

These are user-created multi-instance connections.
Their configured hosts and URLs are subject to Nova's shared outbound egress
policy: local/private/internal targets are blocked unless an administrator has
explicitly allowed them with `NOVA_EGRESS_ALLOWLIST`.

The add/edit flow is unified in one `Settings` screen.

Connection/auth modes remain:

- `none`
- `basic`
- `token` (`Access token` in the UI)
- `api_key`
- `oauth_managed` for MCP only

`Tool.is_active` is no longer part of the model. UI readiness is calculated from the
saved configuration and connection state instead.

### MCP Managed OAuth

For MCP connections that require OAuth:

- select `Managed OAuth`
- use `Connect with OAuth` / `Reconnect with OAuth`
- use `Verify connection` once credentials exist

This flow is explicit in the UI and distinct from the manual `Access token` mode.

### API Services

Custom API connections can define multiple `APIToolOperation` rows with:

- method
- path template
- query parameters
- optional body parameter
- input/output schema

The runtime then exposes them through `api ...` commands.

## Memory Settings

The memory settings page controls:

- embeddings source (`system`, `custom`, `disabled`)
- custom embeddings endpoint/model values
- rebuild confirmation when the effective embeddings provider changes
- document-centric inspection of user memory

Memory is a user-scoped capability shared by agents that have `Memory` enabled. It is
not configured as a connection.

## Task Settings

Task definitions support:

- `cron`
- `email_poll`

Run modes:

- `new_thread`
- `continuous_message`
- `ephemeral`

Templates are prefilled from current runtime prerequisites, including mailbox selection and `/memory/...` documents used by thematic watch flows.
