# Current Context

## Current Work Focus

Implemented webapps preview with a dedicated page and simplified regular layout. Sidebar lists webapps with compact actions. Debounced iframe refresh on updates. State persists per thread.

## Recent Changes

2025-11-10/11:
- Added dedicated preview URL and page: [preview.html](nova/templates/nova/preview.html)
- New view and route: [webapp_views.py](nova/views/webapp_views.py) → preview_webapp, and URL added in [urls.py](nova/urls.py)
- Sidebar “Preview” now navigates to the new page: [files.js](nova/static/js/files.js)
- Regular index page no longer contains split/resizer UI; only message container remains: [index.html](nova/templates/nova/index.html)
- Preview page renders 30/70 split with resizer; persists width per thread; mobile overlay supported via existing JS
- Iframe sandbox updated to allow scripts + same-origin so webapp JS runs; CSP for served webapp responses remains strict (connect-src 'none'): [index.html](nova/templates/nova/index.html), [webapp_views.py](nova/views/webapp_views.py)
- Debounced 400ms refresh on webapp_update with spinner and cache-buster handled by PreviewManager in [thread_management.js](nova/static/js/thread_management.js)
- Webapps list UI: compact buttons and name|slug display: [webapps_list.html](nova/templates/nova/files/webapps_list.html)

Fixes:
- Remove split-container and split-resizer from regular 3-pane view
- Ensure preview pane is visible on preview page init (remove d-none and dispatch webapp_preview_activate)

## Preview Architecture Summary

Flow:
- User clicks Preview in sidebar → navigates to /apps/preview/<thread_id>/<slug>/
- View preview_webapp resolves thread and webapp and renders preview.html with:
  - Left pane: chat UI (messages for selected thread loaded via message_list)
  - Right pane: iframe pointing to /apps/<slug>/ (or external_base variant)
- Close buttons navigate back to index (history.back fallback)

URLs:
- /apps/preview/<thread_id>/<slug>/ → dedicated split page
- /apps/<slug>/ and /apps/<slug>/<path>/ → static file serving with strict CSP

Client state:
- lastThreadId and lastPreviewSlug:<thread_id> persisted in localStorage
- splitWidth:<thread_id> persisted

Realtime:
- task_update message types webapp_public_url and webapp_update already implemented by webapp tool publisher
- webapp_update triggers 400ms debounced iframe reload when slug matches current preview

## Key Decisions

- Use a dedicated preview page to keep index page simple and avoid always rendering split UI
- Keep CSP strict for served webapp responses; allow-same-origin on iframe so client-side JS executes
- Reuse existing PreviewManager and web socket plumbing; initialize from preview page via webapp_preview_activate event

## Next Steps

- Fixes and enhancements:
  - Remove legacy in-page split code paths that are no longer used on index (keep only code needed for preview page)
  - Manage local storage removal when needed to avoid too much storage usage
  - Add name field to WebApp model and populate list and preview title with name
  - Make the webapp list available on mobile
- Tests:
  - Unit test preview_webapp view authorization and 404 cases
  - Template render test for preview.html
  - JS integration test for debounced refresh and cache-busting (if feasible)
- Document the new endpoints and UI flow in developer docs

## Testing Guidelines

- Unit tests run with: `python manage.py test --settings nova.settings_test`
- Do not launch the application in this environment; manual testing is performed by the user
- Focus on code analysis, planning, and documentation