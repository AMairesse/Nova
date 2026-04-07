# Nova - User Settings App

`user_settings/` is the Django app that configures the current Nova runtime for each user.

It owns the UI for:

- providers
- agents
- tools and credentials
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
- attached tools
- delegated sub-agents
- summarization behavior

Important UX rules:

- agents warn when the selected provider/model is verified without tool support
- default bootstrap is role-aware
- the runtime choice is no longer exposed in settings

## Tool Settings

Builtin tools are driven by the internal plugin registry, not by scanning Python modules.

The configure screen is shared across API and MCP tools and uses a unified `connection_mode`.

Supported modes:

- `none`
- `basic`
- `token` (`Access token` in the UI)
- `api_key`
- `oauth_managed` for MCP only

### MCP Managed OAuth

For MCP servers that require OAuth:

- select `Managed OAuth`
- use `Connect with OAuth` / `Reconnect with OAuth`
- use `Verify connection` once credentials exist

This flow is explicit in the UI and distinct from the manual `Access token` mode.

### API Services

Custom API tools can define multiple `APIToolOperation` rows with:

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

Memory itself is stored as Markdown documents and chunks.

## Task Settings

Task definitions support:

- `cron`
- `email_poll`

Run modes:

- `new_thread`
- `continuous_message`
- `ephemeral`

Templates are prefilled from current runtime prerequisites, including mailbox selection and `/memory/...` documents used by thematic watch flows.
