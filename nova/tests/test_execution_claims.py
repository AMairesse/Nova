from threading import Barrier, Thread as WorkerThread
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from unittest import skipUnless
from django.utils import timezone

from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Actor
from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.execution import (
    begin_routine_run,
    claim_interaction,
    claim_task,
    dispatch_task,
)
from nova.tests.factories import create_agent, create_provider


class ExecutionClaimTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("claims", password="pass")
        provider = create_provider(self.user)
        self.agent = create_agent(self.user, provider)
        self.thread = Thread.objects.create(user=self.user, subject="claims")
        self.message = self.thread.add_message("prompt", actor=Actor.USER)

    def _task(self, **kwargs):
        return Task.objects.create(
            user=self.user, thread=self.thread, agent_config=self.agent,
            source_message=self.message, **kwargs
        )

    def test_claim_task_only_first_delivery_wins(self):
        task = self._task(status=TaskStatus.PENDING)
        args = dict(task_id=task.id, user_id=self.user.id, thread_id=self.thread.id,
                    agent_id=self.agent.id, message_id=self.message.id)

        self.assertTrue(claim_task(**args))
        self.assertFalse(claim_task(**args))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.RUNNING)

    def test_stale_executor_cannot_overwrite_cooperative_stop(self):
        from nova.tasks.TaskExecutor import TaskExecutor
        from nova.runtime.outcomes import RuntimeStopped
        task = self._task(status=TaskStatus.RUNNING)
        executor = TaskExecutor.__new__(TaskExecutor)
        executor.task = task
        Task.objects.filter(pk=task.pk).update(stop_requested=True)
        task.status = TaskStatus.COMPLETED
        with self.assertRaises(RuntimeStopped):
            executor._save_active_transition(['status', 'updated_at'])
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertTrue(task.stop_requested)

    def test_claim_task_rejects_stop_requested_and_completed(self):
        stopped = self._task(status=TaskStatus.PENDING, stop_requested=True)
        completed = self._task(status=TaskStatus.COMPLETED)
        common = dict(user_id=self.user.id, thread_id=self.thread.id,
                      agent_id=self.agent.id, message_id=self.message.id)
        self.assertFalse(claim_task(stopped.id, **common))
        self.assertFalse(claim_task(completed.id, **common))

    def test_claim_interaction_only_first_resume_wins(self):
        task = self._task(status=TaskStatus.AWAITING_INPUT)
        interaction = Interaction.objects.create(
            task=task, thread=self.thread, agent_config=self.agent,
            question="Continue?", status=InteractionStatus.ANSWERED,
        )

        self.assertTrue(claim_interaction(interaction.id))
        self.assertFalse(claim_interaction(interaction.id))
        interaction.refresh_from_db()
        self.assertIsNotNone(interaction.resume_claimed_at)

    def test_claim_interaction_rejects_stopped_task(self):
        task = self._task(status=TaskStatus.AWAITING_INPUT, stop_requested=True)
        interaction = Interaction.objects.create(
            task=task, thread=self.thread, agent_config=self.agent,
            question="Continue?", status=InteractionStatus.ANSWERED,
        )

        self.assertFalse(claim_interaction(interaction.id))
        task.refresh_from_db()
        interaction.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.AWAITING_INPUT)
        self.assertIsNone(interaction.resume_claimed_at)

    def test_dispatch_failure_does_not_overwrite_already_running_task(self):
        task = self._task(status=TaskStatus.RUNNING)
        dispatcher = Mock()
        dispatcher.delay.side_effect = RuntimeError("broker unavailable")

        dispatch_task(task, dispatcher)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.RUNNING)

    def test_dispatch_failure_marks_pending_task_retryable(self):
        task = self._task(status=TaskStatus.PENDING)
        dispatcher = Mock()
        dispatcher.delay.side_effect = RuntimeError("broker unavailable")

        dispatch_task(task, dispatcher)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DISPATCH_FAILED)
        self.assertEqual(task.failure_reason, "dispatch")

    def test_begin_routine_run_marks_overlap_skipped(self):
        definition = TaskDefinition.objects.create(
            user=self.user, name="routine", agent=self.agent,
            prompt="run", trigger_type=TaskDefinition.TriggerType.CRON,
            cron_expression="0 8 * * *",
        )
        existing, claimed = begin_routine_run(definition, key="same")
        self.assertTrue(claimed)
        existing.status = "RUNNING"
        existing.save(update_fields=["status", "updated_at"])

        overlap, claimed = begin_routine_run(definition, key="other")

        self.assertFalse(claimed)
        self.assertEqual(overlap.status, "SKIPPED")
        self.assertEqual(RoutineRun.objects.filter(definition=definition).count(), 2)

    def test_routine_run_is_not_completed_by_a_running_task(self):
        definition = TaskDefinition.objects.create(
            user=self.user, name="routine-wait", agent=self.agent,
            prompt="run", trigger_type=TaskDefinition.TriggerType.CRON,
            cron_expression="0 8 * * *",
        )
        run, claimed = begin_routine_run(definition, key="wait")
        task = self._task(status=TaskStatus.RUNNING)
        run.task = task
        run.status = "RUNNING"
        run.save(update_fields=["task", "status", "updated_at"])

        run.refresh_from_db()
        self.assertEqual(run.status, "RUNNING")


@skipUnless(connection.vendor == "postgresql", "row-lock concurrency requires PostgreSQL")
class PostgreSQLClaimConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_workers_cannot_claim_same_task(self):
        User = get_user_model()
        user = User.objects.create_user("concurrent", password="pass")
        provider = create_provider(user)
        agent = create_agent(user, provider)
        thread = Thread.objects.create(user=user, subject="concurrent")
        message = thread.add_message("prompt", actor=Actor.USER)
        task = Task.objects.create(user=user, thread=thread, agent_config=agent,
                                   source_message=message, status=TaskStatus.PENDING)
        barrier = Barrier(2)
        results = []

        def worker():
            close_old_connections()
            barrier.wait()
            results.append(claim_task(task.id, user_id=user.id, thread_id=thread.id,
                                      agent_id=agent.id, message_id=message.id))
            close_old_connections()

        workers = [WorkerThread(target=worker) for _ in range(2)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join()

        self.assertEqual(sorted(results), [False, True])
