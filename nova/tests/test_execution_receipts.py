from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.ActionReceipt import ActionReceipt
from nova.models.Message import Actor
from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.execution import finish_routine_task, start_action_receipt
from nova.tests.factories import create_agent, create_provider


class ExecutionReceiptTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user("receipt-user", password="pass")
        provider = create_provider(self.user)
        self.agent = create_agent(self.user, provider)
        self.thread = Thread.objects.create(user=self.user, subject="receipts")
        self.message = self.thread.add_message("prompt", actor=Actor.USER)

    def _task(self, **kwargs):
        return Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            source_message=self.message,
            **kwargs,
        )

    def _definition_and_failed_run(self, task):
        definition = TaskDefinition.objects.create(
            user=self.user,
            name="receipt routine",
            agent=self.agent,
            prompt="run",
            trigger_type=TaskDefinition.TriggerType.CRON,
            cron_expression="0 8 * * *",
        )
        run = RoutineRun.objects.create(
            user=self.user,
            definition=definition,
            name=definition.name,
            key=f"receipt-{task.pk}",
            task=task,
            status="RUNNING",
        )
        task.status = TaskStatus.FAILED
        task.result = "Command failed"
        task.save(update_fields=["status", "result", "updated_at"])
        return definition, run

    def test_simple_search_without_output_has_no_receipt(self):
        task = self._task()

        start_action_receipt(task, "search nova privacy")

        self.assertFalse(ActionReceipt.objects.filter(task=task).exists())

    def test_search_output_forms_have_receipts(self):
        task = self._task()

        start_action_receipt(task, "search nova --output /tmp/results.json")
        start_action_receipt(task, "search nova --output=/tmp/results.json")

        self.assertEqual(ActionReceipt.objects.filter(task=task).count(), 2)

    def test_search_composition_has_receipt(self):
        task = self._task()

        start_action_receipt(task, "search nova | cat")

        self.assertEqual(ActionReceipt.objects.filter(task=task).count(), 1)

    def test_failed_routine_after_read_only_search_stays_active(self):
        task = self._task()
        definition, _run = self._definition_and_failed_run(task)

        start_action_receipt(task, "search nova")
        finish_routine_task(task)

        definition.refresh_from_db()
        self.assertTrue(definition.is_active)

    def test_failed_routine_with_external_action_is_disabled(self):
        task = self._task()
        definition, _run = self._definition_and_failed_run(task)

        start_action_receipt(task, "calendar create --title meeting")
        finish_routine_task(task)

        definition.refresh_from_db()
        self.assertFalse(definition.is_active)
