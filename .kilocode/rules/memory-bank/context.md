# Current Context

## Current Work Focus

**Completed: Thread UI refactors (Bootstrap-native + deduplication)**:

- Centralized initialization in `NovaApp.bootstrapThreadUI()`
- Removed `DOMContentLoaded` auto-init from modules (notably `responsive.js`)
- Added explicit `bind()` / idempotence guards to avoid duplicate listeners
- Refactored mobile Files/Webapps tabs to Bootstrap 5 native tabs (`data-bs-toggle="tab"`) and replaced manual switching with a `shown.bs.tab` handler
- Extracted duplicated user dropdown menu items into reusable template partial `includes/user_menu_items.html`

**Next**: Validate mobile offcanvas behavior (tabs switching, files toolbar visibility, webapps lazy-load), plus thread switching/files sidebar/websocket regressions.
