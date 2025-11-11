# Current Context

## Current Work Focus

WebApp tool: agents can create per-thread static mini webapps (HTML/CSS/JS) that are securely served under /apps/<slug>/ and previewed side-by-side with the chat. Implementation spans:

- Backend models: WebApp + WebAppFile with strict constraints.
- Builtin tool: WebApp for create/update/read with WebSocket announcements.
- Views/URLs: secure serving, listing, and dedicated preview page.
- Frontend: sidebar Webapps tab, webapps_list partial, and PreviewManager integration.

## Recent Changes

2025-11-10/11:

- Added WebApp model and WebAppFile model:

  - WebApp:
    - User-owned, thread-scoped static mini-apps.
    - Fields: user, thread, slug (uuid hex), created_at, updated_at.
    - Validation: WebApp.user must equal WebApp.thread.user for strict multi-tenancy.
    - Indexes on slug, user, thread.
    - See [WebApp.py](nova/models/WebApp.py:15).

  - WebAppFile:
    - Single-level file paths only, validated by regex.
    - Allowed extensions: .html, .css, .js.
    - Max 200 KB per file; UTF-8 content.
    - unique_together (webapp, path) and index on (webapp, path).
    - Validation prevents hidden files, nested paths, and oversize content.
    - See [WebAppFile.py](nova/models/WebAppFile.py:35).

- Implemented builtin WebApp tool in [webapp.py](nova/tools/builtins/webapp.py:1):

  - Public metadata entry "WebApp" for the tool registry.
  - Core helpers:
    - _get_webapp_by_slug_sync: fetch by user + slug with thread/user preloaded.
    - _create_webapp_sync: create WebApp bound to current user/thread with clean().
    - _get_or_create_file_sync, _list_files_sync, _all_contents_size_sync.
    - _ensure_total_size_within_limit: total cap 600 KB across all files.
    - _upsert_files: validates types, updates/creates files, enforces limits.
  - Tool functions:
    - upsert_webapp(slug, files, agent):
      - If slug is None: create new app.
      - If slug set: validate ownership and same-thread affinity; otherwise error.
      - Upsert files with constraints.
      - Computes public_url using CSRF_TRUSTED_ORIGINS[0]/apps/<slug>/.
      - Publishes webapp_public_url (if first URL) and webapp_update via task WebSocket group, if task_id + channel_layer available.
      - Returns {"slug": slug, "public_url": public_url} or a user-visible error string.
    - read_webapp(slug, agent):
      - Validates ownership and thread affinity.
      - Returns {path: content}.
  - get_functions:
    - Exposes create_webapp, update_webapp, read_webapp as StructuredTools with JSON args_schema.

- Added webapp views and URLs:

  - In [urls.py](nova/urls.py:63):
    - /apps/list/<int:thread_id>/ → webapps_list
    - /apps/preview/<int:thread_id>/<slug:slug>/ → preview_webapp
    - /apps/<slug:slug>/ and /apps/<slug:slug>/<path:path>/ → serve_webapp

  - serve_webapp in [webapp_views.py](nova/views/webapp_views.py:24):
    - Default path "index.html" when omitted.
    - Loads WebApp by slug scoped to request.user; enforces strict multi-tenancy.
    - Loads WebAppFile by path from webapp.files.
    - Uses _guess_mime to set content-type (HTML/CSS/JS fallback).
    - Security headers:
      - Content-Security-Policy:
        - default-src 'self' https:
        - style-src 'self' 'unsafe-inline' https:
        - script-src 'self' 'unsafe-inline' https:
        - img-src 'self' data: https:
        - connect-src 'none'
      - X-Frame-Options: SAMEORIGIN
      - Cache-Control: no-store
    - This keeps agent-authored apps static and offline-only from browser POV.

  - webapps_list in [webapp_views.py](nova/views/webapp_views.py:55):
    - Validates Thread(id=thread_id, user=request.user).
    - Filters WebApp by user and thread, ordered by -updated_at.
    - Builds items with slug, updated_at, and public_url.
      - public_url uses CSRF_TRUSTED_ORIGINS[0]/apps/<slug>/ if configured, else /apps/<slug>/.
    - Renders [webapps_list.html](nova/templates/nova/files/webapps_list.html:1).

  - preview_webapp in [webapp_views.py](nova/views/webapp_views.py:89):
    - Validates thread_id + slug belong to request.user and same thread.
    - Computes public_url (same strategy as webapps_list).
    - Renders [preview.html](nova/templates/nova/preview.html:1) with:
      - thread (for chat),
      - webapp slug, optional name (if model extended), public_url.

- Frontend sidebar and preview integration:

  - Sidebar panel in [sidebar_panel.html](nova/templates/nova/files/sidebar_panel.html:1):
    - Files | Webapps tabs with shared panel:
      - Files tab: upload controls + file tree.
      - Webapps tab: webapps-list-container placeholder.
    - Lightweight inline script toggles visibility and calls FileManager.loadWebappsList when Webapps is shown.

  - FileManager in [files.js](nova/static/js/files.js:1):
    - Tracks currentThreadId and syncs with threadChanged events.
    - Lazy-loads sidebar content HTML and binds:
      - Files/Webapps tabs,
      - Upload buttons,
      - Webapps list loader: GET /apps/list/<thread_id>/ into webapps-list-container.
    - Handles .webapp-preview-btn:
      - On click, navigates to /apps/preview/<thread_id>/<slug>/.
    - Provides activateSplitPreview event dispatch hook (now used by preview layout logic).
    - Ensures sidebar reacts when thread changes and when responsive layout toggles sidebars.

  - Webapps list UI in [webapps_list.html](nova/templates/nova/files/webapps_list.html:1):
    - For each app:
      - Shows name if provided, otherwise slug.
      - Shows last updated timestamp.
      - Buttons:
        - Preview side-by-side (.webapp-preview-btn) → opens dedicated preview page.
        - Open in new tab → direct public_url.

  - Dedicated preview page in [preview.html](nova/templates/nova/preview.html:1):
    - Layout:
      - Full-height row with:
        - Left "chat-pane" (message-container, hydrated by thread_management.js).
        - Vertical resizer (desktop).
        - Right "preview-pane":
          - Toolbar: close, refresh, open in new tab, label using webapp.name or slug, mobile chat toggle.
          - iframe#webapp-iframe with sandbox="allow-scripts allow-same-origin".
          - Loading spinner overlay.
    - JS behavior:
      - Initializes window.NovaApp.urls (reuses chat endpoints).
      - Stores lastThreadId and lastPreviewSlug:<thread_id> in localStorage.
      - On DOMContentLoaded:
        - Sets iframe.src to webapp.public_url with spinner handling.
        - Sets open-in-new-tab href.
        - Ensures preview-pane visible.
        - Dispatches webapp_preview_activate with slug + public_url.
        - Binds close buttons to navigateBack:
          - history.back if possible; else redirect to index.

  - PreviewManager in [thread_management.js](nova/static/js/thread_management.js:1054):
    - Activated on pages where it exists (safe no-op if DOM IDs missing).
    - Responsibilities:
      - Applies persisted split width:
        - Uses CSS var --chat-pane-width.
        - Persists per-thread as splitWidth:<thread_id>.
      - Opens/closes preview:
        - openPreview(slug, url):
          - Stores lastPreviewSlug:<thread_id>.
          - Shows preview-pane, sets iframe src (with spinner), buttons, label.
          - Adapts layout for desktop vs mobile (overlay chat on mobile).
        - closePreview():
          - Hides preview-pane and resizer; expands chat to 100%.
      - Resizer:
        - Mouse and keyboard support; persists width per thread.
      - Events:
        - webapp_preview_activate:
          - Opens preview with given slug/url.
        - webapp_update:
          - Debounced (400ms) refreshIframeIfMatches(slug) with cache-busting, only if iframe slug matches.
        - threadChanged:
          - Restores last preview for that thread if lastPreviewSlug exists; else closes preview.
      - LocalStorage use:
        - lastThreadId, lastPreviewSlug:<thread_id>, splitWidth:<thread_id> (and sidebarTab in FileManager).

- WebSocket integration:

  - Builtin WebApp tool publishes:
    - webapp_public_url: to inform client about the new webapp URL for current task.
    - webapp_update: to trigger preview refresh.
  - StreamingManager in [thread_management.js](nova/static/js/thread_management.js:233):
    - On message type webapp_update:
      - Dispatches CustomEvent('webapp_update', { detail: { slug } }).
    - On message type webapp_public_url:
      - Dispatches CustomEvent('webapp_public_url', { detail: { slug, public_url } }).
  - PreviewManager listens for webapp_update to refresh iframe; other listeners (e.g., index) can handle webapp_public_url.

## Key Decisions

- Scope and isolation:
  - WebApps are strictly per-user and per-thread.
  - WebApp.user must match Thread.user; no cross-tenant or cross-thread sharing via slug.
- Safe content surface:
  - Only small, static HTML/CSS/JS files with strict size and path constraints.
  - No arbitrary directories or binary assets to simplify validation and serving.
- Security:
  - serve_webapp uses a restrictive CSP and disables outbound connections (connect-src 'none') to keep agent-generated apps contained.
  - Iframe sandbox allows scripts and same-origin only; apps cannot easily exfiltrate data.
- UX separation:
  - Dedicated preview page for split view instead of mixing into main index layout.
  - Sidebar Webapps tab provides quick navigation and discovery.
  - PreviewManager centralizes split-preview logic; works with both index and preview context (no hard dependency on always-on preview).

## Planned Fixes and Enhancements

These are the prioritized improvements and how to approach them:

1. WebApp tool URL and base handling

- Problem:
  - upsert_webapp builds public_url from settings.CSRF_TRUSTED_ORIGINS[0]; this may be missing or misaligned in some deployments.
- Plan:
  - In [webapp.py](nova/tools/builtins/webapp.py:167):
    - Implement a helper to compute base URL:
      - Prefer CSRF_TRUSTED_ORIGINS[0] if present.
      - Fallback to a configurable BASE_URL or a relative path "/apps/<slug>/" when no absolute base is available.
    - Ensure public_url is always valid and does not break when CSRF_TRUSTED_ORIGINS is empty.

2. Thread affinity and error messages

- Problem:
  - When slug exists on another thread, tool returns generic "Error with the tool." This is confusing for both agents and users.
- Plan:
  - In upsert_webapp and read_webapp:
    - Keep security check (webapp.thread_id == agent.thread.id).
    - Return precise but safe messages:
      - Example: "The specified webapp belongs to a different conversation. Use the correct slug for this thread."
    - Ensure no sensitive info from other threads is leaked; do not reveal target thread id/user.

3. StructuredTool schemas and guidance

- Problem:
  - args_schema currently allows arbitrary object; constraints are documented but not enforced at schema level.
- Plan:
  - In get_functions in [webapp.py](nova/tools/builtins/webapp.py:199):
    - Keep JSON-schema-compatible structure but improve descriptions:
      - Explicitly state:
        - Paths must be a single filename like "index.html" (no slashes).
        - Only .html/.css/.js files allowed.
        - Content is raw text; do not escape HTML characters.
        - index.html is recommended as entry point.
    - (Optional later) Introduce stricter validation wrappers for schema if compatible with current tooling.

4. Security and headers consistency

- Problem:
  - CSP and sandbox are correct but should be fully documented and consistent.
- Plan:
  - Confirm serve_webapp CSP matches preview iframe sandbox usage.
  - Consider:
    - Adding frame-ancestors 'self' to CSP and relying less on X-Frame-Options.
    - Keeping connect-src 'none' to prevent exfiltration from agent apps.
  - Ensure all responses from serve_webapp set UTF-8 and no-store correctly.

5. Preview UX, navigation, and resilience

- Goals:
  - Consistent closing behavior.
  - Resilient behavior when elements missing.
- Plan:
  - In [preview.html](nova/templates/nova/preview.html:90+):
    - Ensure navigateBack helper is defined on its own line (no accidental concat with dispatch call).
    - Use navigateBack for both:
      - Toolbar close button (#webapp-close-btn).
      - Floating close button (#preview-close-floating).
  - In PreviewManager:
    - Ensure functions safely no-op when DOM elements absent (already mostly true).
    - Keep logic restricted to preview.html; ensure index.html does not unintentionally render split UI elements.
    - Keep label showing webapp.name (if available) with slug for debugging.

6. Sidebar Webapps list and mobile behavior

- Goals:
  - Reliable Webapps tab behavior on desktop and mobile.
- Plan:
  - In [files.js](nova/static/js/files.js:169+):
    - Ensure loadWebappsList:
      - Shows "No thread selected." when no thread.
      - Called only when Webapps tab is active.
      - Refreshes content on threadChanged events (already wired).
  - Ensure responsive layout exposes Webapps tab in mobile sidebar (leveraging existing ResponsiveManager).
  - Maintain consistent empty/error states:
    - No webapps message from [webapps_list.html](nova/templates/nova/files/webapps_list.html:27).
    - Error message when fetch fails.

7. LocalStorage hygiene

- Problem:
  - Multiple keys per thread; no cleanup strategy.
- Plan:
  - In PreviewManager / FileManager:
    - Add lightweight cleanup on thread list reload (future enhancement):
      - Optionally prune keys for threads no longer present.
    - Wrap localStorage access in try/catch (already partially done in preview.html) to avoid breaking when blocked.

8. WebSocket event robustness

- Goals:
  - Prevent crashes if webapp_public_url/webapp_update payloads are malformed.
- Plan:
  - In [thread_management.js](nova/static/js/thread_management.js:264):
    - Keep existing try/except-style guards around event dispatch.
    - Ensure PreviewManager’s webapp_update handler checks slug and iframe presence before reload.

9. Tests

Planned tests (to be implemented in nova/tests):

- Models:
  - WebApp:
    - user/thread ownership validation.
  - WebAppFile:
    - Valid/invalid paths.
    - Allowed extensions only.
    - Max size enforcement.
    - unique_together behavior.
- Builtin tool (webapp.py):
  - create_webapp:
    - Creates app for current thread; respects size limits.
  - update_webapp:
    - Updates only when slug exists and belongs to same user/thread.
    - Fails with clear message when slug missing or wrong-thread.
  - read_webapp:
    - Returns files for valid slug, enforces owner/thread match.
  - WebSocket:
    - With fake agent._resources (task_id, channel_layer), emits webapp_public_url/webapp_update.
    - No crash when missing task_id.
- Views:
  - serve_webapp:
    - 404 when app not found or user mismatch.
    - 404 when file path not present.
    - Asserts CSP and security headers.
  - webapps_list:
    - Only shows apps for given thread and user.
    - 404 on accessing another user’s thread.
  - preview_webapp:
    - 200 for valid (user, thread, slug).
    - 404/403 for mismatches.
- JS (via Jest or Django static integration if available, or manual):
  - PreviewManager:
    - Opens/closes preview on webapp_preview_activate.
    - Debounces iframe refresh on webapp_update.
    - Persists per-thread splitWidth.
  - FileManager:
    - Loads webapps list on Webapps tab activation and thread changes.
    - Handles no-thread and error states.

## Testing Guidelines

- Run tests with:
  - python manage.py test --settings nova.settings_test
- Focus specifically on:
  - WebApp/WebAppFile validation and isolation.
  - WebApp builtin tool (create/update/read, size limits, thread affinity).
  - Webapp views (serve_webapp, webapps_list, preview_webapp) and headers.
  - JS integration for preview and sidebar Webapps UX.
- Do not launch the full application in this environment; manual UI testing performed externally.