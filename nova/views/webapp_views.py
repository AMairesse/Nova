# nova/views/webapp_views.py
import mimetypes

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from nova.models.WebApp import WebApp


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

    # Tight CSP for agent-authored static apps (no XHR/fetch, only inline and same-origin assets)
    csp = (
        "default-src 'self' https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "script-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: https:; "
        "connect-src 'none';"
    )
    response.headers['Content-Security-Policy'] = csp
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Cache-Control'] = 'no-store'
    return response
