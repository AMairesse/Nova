import logging

from celery import shared_task

from nova.notifications.webpush import send_task_notification_to_user

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="send_task_webpush_notification")
def send_task_webpush_notification(
    self,
    *,
    user_id: int,
    task_id: str | int | None,
    thread_id: int | None,
    thread_mode: str | None,
    status: str,
):
    try:
        return send_task_notification_to_user(
            user_id=user_id,
            task_id=task_id,
            thread_id=thread_id,
            thread_mode=thread_mode,
            status=status,
        )
    except Exception as exc:
        logger.exception(
            "WebPush notification task failed user_id=%s task_id=%s status=%s",
            user_id,
            task_id,
            status,
        )
        return {"status": "error", "error": str(exc)}
