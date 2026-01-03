# Current Context

## Current Work Focus

**Completed: JS Single Entrypoint Refactor (Thread UI)** - single bootstrap + no module auto-init:

- Centralized initialization in `NovaApp.bootstrapThreadUI()`
- Removed `DOMContentLoaded` auto-init from modules (notably `responsive.js`)
- Added explicit `bind()` / idempotence guards to avoid duplicate listeners
- Added a single deferred bootstrap script loaded last on thread pages

**Next**: Validate regressions (thread switching, files sidebar, mobile offcanvas, websocket connections) and remove any remaining duplicate listeners if discovered during manual testing.
