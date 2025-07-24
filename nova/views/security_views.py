# nova/views/security.py
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

@require_GET
@ensure_csrf_cookie             # sets the HttpOnly cookie if not present
def csrf_token(request):
    """
    Return a fresh CSRF token. The browser already stored it in an
    HttpOnly cookie, but JS cannot read that; we expose a copy here.
    """
    return JsonResponse({"csrfToken": get_token(request)})
