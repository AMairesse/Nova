from urllib.parse import quote, urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods
from asgiref.sync import async_to_sync

from nova.models.WebApp import WebApp
from nova.models.Thread import Thread
from nova.webapp.service import delete_webapp as delete_live_webapp
from nova.webapp.service import describe_webapp as describe_live_webapp
from nova.utils import compute_external_base, compute_webapp_public_url
from nova.webapp.service import get_live_file_for_public_webapp, get_live_file_for_webapp, load_live_webapp_content
from nova.webapp.service import list_thread_webapps


def _configured_webapp_origin() -> str:
    return str(getattr(settings, "WEBAPP_PUBLIC_ORIGIN", "") or "").strip().rstrip("/")


def _request_host_matches_origin(request, origin: str) -> bool:
    if not origin:
        return False
    parsed = urlsplit(origin)
    return bool(parsed.netloc) and request.get_host().lower() == parsed.netloc.lower()


def _build_webapp_asset_url(slug: str, path: str | None, query_string: str = "") -> str:
    target = compute_webapp_public_url(slug)
    cleaned_path = str(path or "").lstrip("/")
    if cleaned_path:
        target = f"{target}{quote(cleaned_path, safe='/')}"
    if query_string:
        target = f"{target}?{query_string}"
    return target


def _webapp_frame_ancestors() -> str:
    ancestors = ["'self'"]
    main_origin = compute_external_base()
    public_origin = _configured_webapp_origin()
    if main_origin and main_origin != public_origin:
        ancestors.append(main_origin)
    return " ".join(ancestors)


def _webapp_csp(*, public_origin_request: bool) -> str:
    connect_src = "'self'" if public_origin_request else "'none'"
    return (
        "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        f"connect-src {connect_src}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        f"frame-ancestors {_webapp_frame_ancestors()}; "
        "form-action 'none';"
    )


@xframe_options_exempt
def serve_webapp(request, slug: str, path: str | None = None):
    """Serve a static file belonging to a live WebApp."""
    webapp_origin = _configured_webapp_origin()
    is_webapp_origin = _request_host_matches_origin(request, webapp_origin)

    if webapp_origin and not is_webapp_origin:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        live_file = get_live_file_for_webapp(user=request.user, slug=slug, requested_path=path)
        if live_file is None:
            raise Http404("Webapp file not found.")
        return HttpResponseRedirect(
            _build_webapp_asset_url(slug, path, request.META.get("QUERY_STRING", ""))
        )

    if is_webapp_origin:
        live_file = get_live_file_for_public_webapp(slug=slug, requested_path=path)
    else:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        live_file = get_live_file_for_webapp(user=request.user, slug=slug, requested_path=path)

    if live_file is None:
        raise Http404("Webapp file not found.")

    content = load_live_webapp_content(live_file)
    mime = str(live_file.mime_type or "application/octet-stream")
    if mime.startswith("text/") or mime in {"application/javascript", "application/json", "application/manifest+json"}:
        response = HttpResponse(content, content_type=f"{mime}; charset=utf-8")
    else:
        response = HttpResponse(content, content_type=mime)

    response.headers['Content-Security-Policy'] = _webapp_csp(public_origin_request=is_webapp_origin)
    response.headers['Cache-Control'] = 'no-store'
    return response


@login_required
def webapps_list(request, thread_id: int):
    """
    Return a server-rendered partial listing webapps for the given thread.
    Intended for sidebar rendering (Files | Webapps toggle).
    """
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    items = async_to_sync(list_thread_webapps)(user=request.user, thread=thread)

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
    get_object_or_404(WebApp, user=request.user, thread=thread, slug=slug)
    payload = async_to_sync(describe_live_webapp)(user=request.user, thread=thread, slug=slug)

    context = {
        "thread": thread,
        "webapp": {
            "slug": payload["slug"],
            "public_url": payload["public_url"],
            "name": payload["name"],
            "status": payload["status"],
            "status_detail": payload["status_detail"],
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
