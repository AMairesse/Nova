import json
import logging
from typing import Any

from django.conf import settings
from django.utils import timezone

from nova.models.PushSubscription import PushSubscription
from nova.models.UserObjects import UserParameters

logger = logging.getLogger(__name__)


def build_server_state() -> dict[str, Any]:
    server_enabled = bool(getattr(settings, "WEBPUSH_ENABLED", False))
    vapid_public_key = (getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or "").strip()
    vapid_private_key = (getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or "").strip()
    vapid_subject = (getattr(settings, "WEBPUSH_VAPID_SUBJECT", "") or "").strip()
    server_configured = bool(vapid_public_key and vapid_private_key and vapid_subject)

    if not server_enabled:
        server_state = "disabled"
    elif not server_configured:
        server_state = "misconfigured"
    else:
        server_state = "ready"

    return {
        "server_enabled": server_enabled,
        "server_configured": server_configured,
        "server_state": server_state,
        "vapid_public_key": vapid_public_key if server_state == "ready" else None,
    }


def cleanup_invalid_subscription(subscription: PushSubscription, reason: str | None = None) -> None:
    subscription.is_active = False
    subscription.last_error = reason or "push endpoint is no longer valid"
    subscription.save(update_fields=["is_active", "last_error", "updated_at"])


def _build_target_url(thread_id: int | None, thread_mode: str | None) -> str:
    if str(thread_mode or "").lower() == "continuous":
        return "/continuous/?from_push=1"
    if thread_id:
        return f"/?thread_id={thread_id}&from_push=1"
    return "/?from_push=1"


def _build_notification_payload(
    *,
    task_id: str | int | None,
    thread_id: int | None,
    thread_mode: str | None,
    status: str,
) -> dict[str, Any]:
    is_failed = str(status).lower() == "failed"
    body = "Your request failed." if is_failed else "Your request is complete."
    title = "Nova"
    target_url = _build_target_url(thread_id, thread_mode)

    return {
        "title": title,
        "body": body,
        "tag": f"nova-task-{task_id or 'unknown'}",
        "data": {
            "url": target_url,
            "thread_id": thread_id,
            "thread_mode": thread_mode,
            "task_id": str(task_id) if task_id is not None else None,
            "status": "failed" if is_failed else "completed",
        },
    }


def send_task_notification_to_user(
    *,
    user_id: int,
    task_id: str | int | None,
    thread_id: int | None,
    thread_mode: str | None,
    status: str,
) -> dict[str, Any]:
    state = build_server_state()
    if state["server_state"] != "ready":
        return {"status": "skipped", "reason": f"server_{state['server_state']}"}

    user_opt_in = bool(
        UserParameters.objects.filter(user_id=user_id)
        .values_list("task_notifications_enabled", flat=True)
        .first()
    )
    if not user_opt_in:
        return {"status": "skipped", "reason": "user_opt_out"}

    subscriptions = list(
        PushSubscription.objects.filter(user_id=user_id, is_active=True).only(
            "id", "endpoint", "p256dh", "auth", "last_error"
        )
    )
    if not subscriptions:
        return {"status": "skipped", "reason": "no_subscription"}

    payload = _build_notification_payload(
        task_id=task_id,
        thread_id=thread_id,
        thread_mode=thread_mode,
        status=status,
    )

    try:
        from pywebpush import webpush
    except Exception:
        logger.exception("pywebpush is unavailable while WEBPUSH is enabled")
        return {"status": "error", "reason": "pywebpush_unavailable"}

    sent = 0
    failed = 0
    invalidated = 0
    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", ""),
                vapid_claims={"sub": getattr(settings, "WEBPUSH_VAPID_SUBJECT", "")},
                ttl=60,
            )
            PushSubscription.objects.filter(id=sub.id).update(
                last_success_at=timezone.now(),
                last_error="",
                updated_at=timezone.now(),
            )
            sent += 1
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                cleanup_invalid_subscription(sub, reason=f"push endpoint invalid ({status_code})")
                invalidated += 1
            else:
                PushSubscription.objects.filter(id=sub.id).update(
                    last_error=str(exc),
                    updated_at=timezone.now(),
                )
            failed += 1

    return {
        "status": "ok",
        "sent": sent,
        "failed": failed,
        "invalidated": invalidated,
    }
