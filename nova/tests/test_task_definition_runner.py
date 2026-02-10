from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, TestCase

from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.task_definition_runner import (
    _prepare_thread_and_message,
    execute_agent_task_definition,
    build_email_prompt_variables,
    render_prompt_template,
)
from nova.tests.factories import create_agent, create_provider, create_user


class TaskDefinitionRunnerUtilsTests(SimpleTestCase):
    def test_render_prompt_template_replaces_known_variables(self):
        template = "Count={{ new_email_count }}\nList={{ new_emails_markdown }}"
        rendered = render_prompt_template(
            template,
            variables={"new_email_count": 2, "new_emails_markdown": "- a\n- b"},
        )

        self.assertIn("Count=2", rendered)
        self.assertIn("List=- a", rendered)

    def test_render_prompt_template_missing_variable_becomes_empty_string(self):
        rendered = render_prompt_template("Hello {{ missing_key }}!", variables={})
        self.assertEqual(rendered, "Hello !")

    def test_build_email_prompt_variables(self):
        headers = [
            {"uid": 10, "from": "alice@example.com", "subject": "A", "date": "2026-02-08T10:00:00+00:00"},
            {"uid": 11, "from": "bob@example.com", "subject": "B", "date": "2026-02-08T10:01:00+00:00"},
        ]
        vars_ = build_email_prompt_variables(headers)
        self.assertEqual(vars_["new_email_count"], 2)
        self.assertEqual(len(vars_["new_emails_json"]), 2)
        self.assertIn("uid=10", vars_["new_emails_markdown"])


class TaskDefinitionRunnerExecutionTests(TestCase):
    def setUp(self):
        self.user = create_user(username="runner-exec", email="runner-exec@example.com")
        self.provider = create_provider(self.user, name="runner-provider")
        self.agent = create_agent(self.user, self.provider, name="runner-agent")

    def _build_task_definition(self, *, run_mode=TaskDefinition.RunMode.NEW_THREAD, prompt="Hello {{ name }}"):
        task_def = TaskDefinition(
            user=self.user,
            name=f"runner-{run_mode}",
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=self.agent,
            prompt=prompt,
            run_mode=run_mode,
            cron_expression="0 8 * * *",
            timezone="UTC",
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()
        return task_def

    def test_prepare_thread_and_message_new_thread_returns_non_ephemeral(self):
        task_def = self._build_task_definition(run_mode=TaskDefinition.RunMode.NEW_THREAD, prompt="Ping")

        thread, message, ephemeral = _prepare_thread_and_message(task_def, "Ping")

        self.assertFalse(ephemeral)
        self.assertEqual(thread.mode, Thread.Mode.THREAD)
        self.assertEqual(thread.subject, task_def.name)
        self.assertEqual(message.text, "Ping")

    @patch("nova.tasks.task_definition_runner._prepare_continuous_message")
    def test_prepare_thread_and_message_continuous_uses_helper(self, mocked_prepare_continuous):
        task_def = self._build_task_definition(
            run_mode=TaskDefinition.RunMode.CONTINUOUS_MESSAGE,
            prompt="Ping",
        )
        fake_thread = Thread.objects.create(user=self.user, subject="Continuous", mode=Thread.Mode.CONTINUOUS)
        fake_message = fake_thread.add_message("Ping", actor=Actor.USER)
        mocked_prepare_continuous.return_value = (fake_thread, fake_message)

        thread, message, ephemeral = _prepare_thread_and_message(task_def, "Ping")

        self.assertEqual(thread.id, fake_thread.id)
        self.assertEqual(message.id, fake_message.id)
        self.assertFalse(ephemeral)
        mocked_prepare_continuous.assert_called_once_with(task_def, "Ping")

    def test_execute_agent_task_definition_raises_on_empty_rendered_prompt(self):
        task_def = self._build_task_definition(prompt="{{ missing }}")
        with self.assertRaisesMessage(ValueError, "Rendered prompt is empty"):
            execute_agent_task_definition(task_def)

    @patch("nova.tasks.tasks.AgentTaskExecutor")
    def test_execute_agent_task_definition_success_marks_task_completed(self, mocked_executor_cls):
        task_def = self._build_task_definition(prompt="Hello {{ name }}")

        mocked_executor = mocked_executor_cls.return_value
        mocked_executor.execute_or_resume = AsyncMock(return_value=None)

        result = execute_agent_task_definition(task_def, variables={"name": "Alice"})

        self.assertEqual(result["status"], "ok")
        self.assertIn("task_id", result)
        self.assertIn("thread_id", result)
        mocked_executor.execute_or_resume.assert_called_once()

    @patch("nova.tasks.tasks.AgentTaskExecutor")
    def test_execute_agent_task_definition_ephemeral_deletes_thread(self, mocked_executor_cls):
        task_def = self._build_task_definition(
            run_mode=TaskDefinition.RunMode.EPHEMERAL,
            prompt="Ephemeral run",
        )
        mocked_executor = mocked_executor_cls.return_value
        mocked_executor.execute_or_resume = AsyncMock(return_value=None)

        result = execute_agent_task_definition(task_def)
        thread_id = result["thread_id"]
        self.assertFalse(Thread.objects.filter(id=thread_id).exists())

    def test_execute_agent_task_definition_raises_when_task_failed(self):
        task_def = self._build_task_definition(prompt="Make this fail")

        class FailingExecutor:
            def __init__(self, task, user, thread, agent_config, prompt, source_message_id=None):
                self.task = task

            async def execute_or_resume(self):
                self.task.status = TaskStatus.FAILED
                self.task.result = "runner failed"

        with (
            patch("nova.tasks.tasks.AgentTaskExecutor", FailingExecutor),
            patch.object(Task, "refresh_from_db", autospec=True, return_value=None),
        ):
            with self.assertRaisesMessage(RuntimeError, "runner failed"):
                execute_agent_task_definition(task_def)
