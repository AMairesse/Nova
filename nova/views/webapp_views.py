from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from asgiref.sync import async_to_sync

from nova.models.WebApp import WebApp
from nova.models.Thread import Thread
from nova.utils import compute_webapp_public_url
from nova.webapp.service import delete_webapp as delete_live_webapp
from nova.webapp.service import get_live_file_for_webapp, load_live_webapp_content


@login_required
def serve_webapp(request, slug: str, path: str | None = None):
    """Serve a static file belonging to a user-owned live WebApp."""
    live_file = get_live_file_for_webapp(user=request.user, slug=slug, requested_path=path)
    if live_file is None:
        raise Http404("Webapp file not found.")

    content = load_live_webapp_content(live_file)
    mime = str(live_file.mime_type or "application/octet-stream")
    if mime.startswith("text/") or mime in {"application/javascript", "application/json", "application/manifest+json"}:
        response = HttpResponse(content, content_type=f"{mime}; charset=utf-8")
    else:
        response = HttpResponse(content, content_type=mime)

    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'self'; "
        "form-action 'self';"
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
        .only("slug", "name", "updated_at")
    )

    # Build public URLs using shared helper to avoid drift with tool behavior
    items = []
    for app in apps:
        slug = app.slug
        public_url = compute_webapp_public_url(slug)
        items.append(
            {
                "slug": slug,
                "name": (app.name or "").strip(),
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
            "name": (webapp.name or "").strip(),
        },
    }
    return render(request, "nova/preview.html", context)


@csrf_protect
@require_http_methods(["DELETE"])
@login_required
def delete_webapp(request, thread_id: int, slug: str):
    """
    Delete a user-owned webapp from a specific thread.
    Returns 404 for unauthorized/missing resources to preserve tenant isolation.
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    get_object_or_404(
        WebApp,
        user=request.user,
        thread=thread,
        slug=slug,
    )
    result = async_to_sync(delete_live_webapp)(
        user=request.user,
        thread=thread,
        slug=slug,
    )
    return JsonResponse({"status": result["status"]})
