# nova/views/webapp_views.py
import mimetypes

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from nova.models.WebApp import WebApp
from nova.models.Thread import Thread
from nova.utils.webapp_urls import compute_webapp_public_url


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    if path.endswith('.html'):
        return 'text/html'
    if path.endswith('.css'):
        return 'text/css'
    return 'application/javascript'


@login_required
def serve_webapp(request, slug: str, path: str | None = None):
    """Serve a static file belonging to a user-owned WebApp."""
    if not path:
        path = 'index.html'

    # Strict multi-tenancy: only owner's apps are visible
    webapp = get_object_or_404(
        WebApp.objects.select_related('user', 'thread'),
        slug=slug,
        user=request.user,
    )
    file_obj = get_object_or_404(webapp.files, path=path)

    mime = _guess_mime(path)
    response = HttpResponse(file_obj.content, content_type=f"{mime}; charset=utf-8")

    # Tight CSP for agent-authored static apps:
    # - No outbound XHR/fetch (connect-src 'none')
    # - Only same-origin/https scripts and styles (inline allowed to keep UX simple)
    # - Restricted framing to same-origin only
    csp = (
        "default-src 'self' https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "script-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: https:; "
        "connect-src 'none'; "
        "frame-ancestors 'self';"
    )
    response.headers['Content-Security-Policy'] = csp
    # Keep X-Frame-Options for compatibility; CSP frame-ancestors is the primary control.
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Cache-Control'] = 'no-store'
    return response


@login_required
def webapps_list(request, thread_id: int):
    """
    Return a server-rendered partial listing webapps for the given thread.
    Intended for sidebar rendering (Files | Webapps toggle).
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    apps = (
        WebApp.objects.filter(user=request.user, thread=thread)
        .order_by("-updated_at")
        .only("slug", "updated_at")
    )

    # Build public URLs using shared helper to avoid drift with tool behavior
    items = []
    for app in apps:
        slug = app.slug
        public_url = compute_webapp_public_url(slug)
        items.append(
            {
                "slug": slug,
                "updated_at": app.updated_at,
                "public_url": public_url,
            }
        )

    return render(
        request,
        "nova/files/webapps_list.html",
        {"thread": thread, "webapps": items},
    )


@login_required
def preview_webapp(request, thread_id: int, slug: str):
    """
    Full-page preview that shows a 30/70 split:
    - Left: the selected thread's chat UI
    - Right: iframe of the selected webapp
    Includes a close button to return to the regular display.
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    webapp = get_object_or_404(WebApp, user=request.user, thread=thread, slug=slug)

    public_url = compute_webapp_public_url(slug)

    context = {
        "thread": thread,
        "webapp": {
            "slug": slug,
            "public_url": public_url,
            "name": getattr(webapp, "name", None),
        },
    }
    return render(request, "nova/preview.html", context)
