from unittest.mock import patch

from django.test import TestCase

from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.execution import NO_CHANGE_RESULT, finish_routine_task
from nova.tasks.task_definition_runner import execute_agent_task_definition
from nova.tests.factories import create_agent, create_provider, create_user


class RoutineOutcomeTests(TestCase):
    def setUp(self):
        self.user = create_user(username="routine-outcomes")
        self.provider = create_provider(self.user, name="routine-provider")
        self.agent = create_agent(self.user, self.provider, name="routine-agent")

    def _definition(self, *, name="routine", prompt="Run {{ previous_result }} {{ routine_id }}"):
        return TaskDefinition.objects.create(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=self.agent,
            prompt=prompt,
            cron_expression="0 8 * * *",
            timezone="UTC",
        )

    def test_completed_result_survives_thread_deletion(self):
        definition = self._definition()
        thread = Thread.objects.create(user=self.user, subject=definition.name)
        task = Task.objects.create(
            user=self.user, thread=thread, agent_config=self.agent,
            status=TaskStatus.COMPLETED, result="Durable answer",
        )
        run = RoutineRun.objects.create(
            user=self.user, definition=definition, name=definition.name,
            key="outcome-survives", task=task,
        )

        finish_routine_task(task)
        thread.delete()
        run.refresh_from_db()

        self.assertEqual(run.result, "Durable answer")

    def test_no_change_archives_classic_thread_and_keeps_ordinary_sentinel_visible(self):
        definition = self._definition(name="no-change")
        thread = Thread.objects.create(user=self.user, subject="classic")
        task = Task.objects.create(
            user=self.user, thread=thread, agent_config=self.agent,
            status=TaskStatus.COMPLETED, result=NO_CHANGE_RESULT,
        )
        RoutineRun.objects.create(
            user=self.user, definition=definition, name=definition.name,
            key="no-change-classic", task=task,
        )

        finish_routine_task(task)
        thread.refresh_from_db()
        self.assertIsNotNone(thread.archived_at)

        ordinary_thread = Thread.objects.create(user=self.user, subject="ordinary")
        ordinary_task = Task.objects.create(
            user=self.user, thread=ordinary_thread, agent_config=self.agent,
            status=TaskStatus.COMPLETED, result=NO_CHANGE_RESULT,
        )
        finish_routine_task(ordinary_task)
        ordinary_thread.refresh_from_db()
        self.assertIsNone(ordinary_thread.archived_at)

    @patch("nova.tasks.tasks.execute_agent_task_with_executor")
    def test_prompt_uses_latest_same_tenant_completed_result(self, execute):
        definition = self._definition(prompt="{{ previous_result }}|{{ previous_run_at }}|{{ routine_id }}")
        RoutineRun.objects.create(
            user=self.user, definition=definition, name=definition.name,
            key="previous", status=TaskStatus.COMPLETED, result="same tenant result",
        )
        other_user = create_user(username="other-routine-outcomes")
        other_provider = create_provider(other_user, name="other-provider")
        other_agent = create_agent(other_user, other_provider, name="other-agent")
        other_definition = TaskDefinition.objects.create(
            user=other_user, name="other", agent=other_agent, prompt="other",
            cron_expression="0 8 * * *", timezone="UTC",
        )
        RoutineRun.objects.create(
            user=other_user, definition=other_definition, name="other",
            key="other-result", status=TaskStatus.COMPLETED, result="wrong tenant result",
        )

        seen = {}

        def capture(task, _user, _thread, _agent, prompt, **_kwargs):
            seen["prompt"] = prompt
            task.status = TaskStatus.COMPLETED
            task.save(update_fields=["status", "updated_at"])

        execute.side_effect = capture
        result = execute_agent_task_definition(definition)

        self.assertEqual(result["status"], "ok")
        self.assertIn("same tenant result", seen["prompt"])
        self.assertNotIn("wrong tenant result", seen["prompt"])
        self.assertIn(str(definition.pk), seen["prompt"])
