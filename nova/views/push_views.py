import datetime as dt
import json

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from nova.models.PushSubscription import PushSubscription
from nova.models.UserObjects import UserParameters
from nova.notifications.webpush import build_server_state


def _parse_json_body(request):
    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        payload = json.loads(raw)
    except Exception:
        return None, JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    return payload, None


def _parse_expiration_time(value):
    if value in (None, "", 0):
        return None

    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value) / 1000.0, tz=dt.timezone.utc)

    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(normalized)

    raise ValueError("invalid_expiration_time")


@require_GET
@login_required(login_url="login")
def push_config(request):
    state = build_server_state()
    user_params, _ = UserParameters.objects.get_or_create(user=request.user)
    return JsonResponse(
        {
            "server_enabled": state["server_enabled"],
            "server_configured": state["server_configured"],
            "server_state": state["server_state"],
            "vapid_public_key": state["vapid_public_key"],
            "user_opt_in": bool(user_params.task_notifications_enabled),
        }
    )


@csrf_protect
@require_http_methods(["POST", "DELETE"])
@login_required(login_url="login")
def push_subscriptions(request):
    payload, error = _parse_json_body(request)
    if error:
        return error

    endpoint = str(payload.get("endpoint", "")).strip()
    if not endpoint:
        return JsonResponse({"error": "endpoint_required"}, status=400)

    if request.method == "DELETE":
        updated = PushSubscription.objects.filter(
            endpoint=endpoint,
            user=request.user,
            is_active=True,
        ).update(is_active=False, last_error="", updated_at=timezone.now())
        if not updated:
            return JsonResponse({"error": "not_found"}, status=404)
        return JsonResponse({"status": "ok"})

    state = build_server_state()
    if state["server_state"] != "ready":
        return JsonResponse({"error": "server_not_ready", "server_state": state["server_state"]}, status=503)

    keys = payload.get("keys") or {}
    p256dh = str(keys.get("p256dh", "")).strip()
    auth = str(keys.get("auth", "")).strip()
    if not p256dh or not auth:
        return JsonResponse({"error": "invalid_keys"}, status=400)

    try:
        expiration_time = _parse_expiration_time(payload.get("expirationTime"))
    except Exception:
        return JsonResponse({"error": "invalid_expiration_time"}, status=400)

    user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:512]
    try:
        with transaction.atomic():
            PushSubscription.objects.update_or_create(
                user=request.user,
                endpoint=endpoint,
                defaults={
                    "p256dh": p256dh,
                    "auth": auth,
                    "expiration_time": expiration_time,
                    "user_agent": user_agent,
                    "is_active": True,
                    "last_error": "",
                },
            )
    except IntegrityError:
        # Endpoint exists but is not owned by current user (global unique endpoint).
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse({"status": "ok"})
