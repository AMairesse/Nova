from django.http import HttpResponse
from django.conf import settings
from django.db import connections
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured


def healthz(request):
    """
    Healthcheck endpoint: Returns 200 if DB and critical services are healthy.
    Only enabled in DEBUG mode.
    """
    if not settings.DEBUG:
        raise ImproperlyConfigured("Healthcheck is only available in DEBUG mode.")

    # Check DB connection (dummy query)
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("Database unavailable", status=503)

    # Check Redis (if using cache, adjust if not)
    try:
        cache = caches['default']  # Assuming Redis is your default cache
        cache.set('healthcheck_test', 'ok', timeout=1)
        if cache.get('healthcheck_test') != 'ok':
            raise ValueError("Redis cache test failed")
    except Exception:
        return HttpResponse("Redis unavailable", status=503)

    # Add checks for other services if needed (e.g., MinIO via a ping)

    return HttpResponse("OK", status=200)
