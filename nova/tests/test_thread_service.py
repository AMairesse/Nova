from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from nova.exec_runner.shared import ExecRunnerError
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tests.factories import create_user
from nova.threads.service import ThreadDeletionError, delete_thread_for_user


class ThreadServiceTests(TestCase):
    def setUp(self):
        self.user = create_user(username="thread-owner")
        self.other = create_user(username="other-owner")

    def test_delete_thread_for_user_rejects_non_owner(self):
        thread = Thread.objects.create(user=self.user, subject="Owned")

        with self.assertRaises(ThreadDeletionError) as context:
            delete_thread_for_user(thread, self.other)

        self.assertEqual(context.exception.status_code, 404)
        self.assertTrue(Thread.objects.filter(pk=thread.pk).exists())

    def test_delete_thread_for_user_rejects_active_running_task(self):
        thread = Thread.objects.create(user=self.user, subject="Busy")
        Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)

        with self.assertRaises(ThreadDeletionError) as context:
            delete_thread_for_user(thread, self.user)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("active tasks", str(context.exception))
        self.assertTrue(Thread.objects.filter(pk=thread.pk).exists())

    @override_settings(NOVA_RUNNING_TASK_STALE_AFTER_SECONDS=60)
    def test_delete_thread_for_user_ignores_stale_running_task(self):
        thread = Thread.objects.create(user=self.user, subject="Stale")
        thread_id = thread.id
        task = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        Task.objects.filter(id=task.id).update(updated_at=timezone.now() - timedelta(minutes=5))

        with patch("nova.threads.service.async_to_sync") as mocked_async_to_sync:
            cleanup = Mock()
            mocked_async_to_sync.return_value = cleanup

            delete_thread_for_user(thread, self.user)

        self.assertFalse(Thread.objects.filter(pk=thread.pk).exists())
        cleanup.assert_called_once_with(self.user.id, thread_id)

    @patch("nova.threads.service.logger")
    @patch("nova.threads.service.async_to_sync")
    def test_delete_thread_for_user_logs_exec_runner_cleanup_failure_but_keeps_delete(
        self,
        mocked_async_to_sync,
        mocked_logger,
    ):
        thread = Thread.objects.create(user=self.user, subject="Cleanup warning")
        cleanup = Mock(side_effect=ExecRunnerError("runner down"))
        mocked_async_to_sync.return_value = cleanup

        delete_thread_for_user(thread, self.user)

        self.assertFalse(Thread.objects.filter(pk=thread.pk).exists())
        mocked_logger.warning.assert_called_once()
