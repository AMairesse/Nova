from __future__ import annotations

import logging

from asgiref.sync import async_to_sync

from nova.exec_runner import service as exec_runner_service
from nova.exec_runner.shared import ExecRunnerError
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tasks.runtime_state import reconcile_stale_running_tasks

logger = logging.getLogger(__name__)


class ThreadDeletionError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def delete_thread_for_user(thread: Thread, user) -> None:
    if getattr(thread, "user_id", None) != getattr(user, "id", None):
        raise ThreadDeletionError("Thread not found.", status_code=404)

    thread_id = thread.id
    user_id = user.id

    reconcile_stale_running_tasks(thread=thread, user=user)

    running_tasks = Task.objects.filter(
        thread=thread,
        user=user,
        status=TaskStatus.RUNNING,
    )
    if running_tasks.exists():
        raise ThreadDeletionError(
            "Cannot delete thread with active tasks. Please wait for the current run to finish."
        )

    thread.delete()

    try:
        async_to_sync(exec_runner_service.delete_thread_sandbox_sessions)(user_id, thread_id)
    except ExecRunnerError as exc:
        logger.warning(
            "Thread %s deleted but exec-runner session cleanup failed for user %s: %s",
            thread_id,
            user_id,
            exc,
        )
