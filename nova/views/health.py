import os

import redis
from django.db import connections
from django.http import HttpResponse


def _check_database() -> None:
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_redis() -> None:
    redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        socket_connect_timeout=1,
        socket_timeout=1,
    ).ping()


def healthz(request):
    """
    Readiness endpoint for container orchestration and reverse proxies.
    """
    try:
        _check_database()
    except Exception:
        return HttpResponse("Database unavailable", status=503)

    try:
        _check_redis()
    except Exception:
        return HttpResponse("Redis unavailable", status=503)

    return HttpResponse("OK", status=200)
