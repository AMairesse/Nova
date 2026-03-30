import datetime as dt
import logging

from django.conf import settings
from django.utils import timezone

from nova.models.Task import Task, TaskStatus

logger = logging.getLogger(__name__)

DEFAULT_RUNNING_TASK_STALE_AFTER_SECONDS = 15 * 60
ORPHANED_TASK_RESULT = (
    "system_error: Task was interrupted before completion and has been marked as failed."
)


def get_running_task_stale_after_seconds() -> int:
    raw_value = getattr(
        settings,
        "NOVA_RUNNING_TASK_STALE_AFTER_SECONDS",
        DEFAULT_RUNNING_TASK_STALE_AFTER_SECONDS,
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_RUNNING_TASK_STALE_AFTER_SECONDS
    return max(value, 60)


def get_running_task_stale_cutoff(*, now=None):
    reference_time = now or timezone.now()
    return reference_time - dt.timedelta(seconds=get_running_task_stale_after_seconds())


def get_stale_running_tasks_queryset(*, thread=None, user=None, now=None):
    queryset = Task.objects.filter(
        status=TaskStatus.RUNNING,
        updated_at__lt=get_running_task_stale_cutoff(now=now),
    )
    if thread is not None:
        queryset = queryset.filter(thread=thread)
    if user is not None:
        queryset = queryset.filter(user=user)
    return queryset


def reconcile_stale_running_tasks(*, thread=None, user=None, now=None) -> list[int]:
    reference_time = now or timezone.now()
    stale_tasks = list(
        get_stale_running_tasks_queryset(
            thread=thread,
            user=user,
            now=reference_time,
        ).only("id", "progress_logs", "status", "result")
    )
    reconciled_ids: list[int] = []
    timestamp = str(reference_time.astimezone(dt.timezone.utc))

    for task in stale_tasks:
        progress_logs = list(task.progress_logs or [])
        progress_logs.append(
            {
                "step": "Task marked as failed after its runtime heartbeat expired",
                "category": "system_error",
                "timestamp": timestamp,
                "severity": "error",
                "error_details": {
                    "type": "OrphanedTask",
                    "message": ORPHANED_TASK_RESULT,
                },
            }
        )
        task.status = TaskStatus.FAILED
        task.result = ORPHANED_TASK_RESULT
        task.progress_logs = progress_logs
        task.save(update_fields=["status", "result", "progress_logs", "updated_at"])
        reconciled_ids.append(task.id)

    if reconciled_ids:
        logger.warning(
            "Reconciled %s stale running task(s): %s",
            len(reconciled_ids),
            reconciled_ids,
        )

    return reconciled_ids
