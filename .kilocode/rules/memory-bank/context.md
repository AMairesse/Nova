# Current Context

## Current Work Focus

WebApp tool: agents can create per-thread static mini webapps (HTML/CSS/JS) that are securely served under /apps/<slug>/ and previewed side-by-side with the chat. Implementation spans:

- Backend models: WebApp + WebAppFile with strict constraints.
- Builtin tool: WebApp for create/update/read with WebSocket announcements.
- Views/URLs: secure serving, listing, and dedicated preview page.
- Frontend: sidebar Webapps tab, webapps_list partial, and PreviewManager integration.
