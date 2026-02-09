from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.tasks.tasks import (
    TRIGGER_TASK_MAX_RETRIES,
    compute_trigger_retry_countdown,
    poll_task_definition_email,
    run_task_definition_cron,
    run_task_definition_maintenance,
    schedule_trigger_task_retry,
)


User = get_user_model()


class TaskDefinitionTaskRunnerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="runner-user", password="x")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="provider",
            provider_type=ProviderType.OLLAMA,
            model="llama3.2",
            base_url="http://localhost:11434",
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="runner-agent",
            llm_provider=self.provider,
            system_prompt="You are helpful.",
        )
        self.email_tool = Tool.objects.create(
            user=self.user,
            name="Email",
            description="Email tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )

    def _create_agent_cron_task(self, name="agent-cron"):
        task_def = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=self.agent,
            prompt="Run a quick check.",
            run_mode=TaskDefinition.RunMode.NEW_THREAD,
            cron_expression="0 6 * * *",
            timezone="UTC",
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()
        return task_def

    def _create_agent_email_task(self, name="agent-email", poll_interval=5):
        task_def = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
            agent=self.agent,
            prompt="Handle new emails.",
            run_mode=TaskDefinition.RunMode.CONTINUOUS_MESSAGE,
            email_tool=self.email_tool,
            poll_interval_minutes=poll_interval,
            timezone="UTC",
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()
        return task_def

    def _create_maintenance_task(
        self,
        name="maintenance-task",
        maintenance_task="continuous_nightly_daysegment_summaries_for_user",
    ):
        task_def = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.MAINTENANCE,
            trigger_type=TaskDefinition.TriggerType.CRON,
            maintenance_task=maintenance_task,
            cron_expression="0 2 * * *",
            timezone="UTC",
            run_mode=TaskDefinition.RunMode.EPHEMERAL,
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()
        return task_def

    def test_compute_trigger_retry_countdown_uses_exponential_backoff_with_cap(self):
        self.assertEqual(compute_trigger_retry_countdown(0), 30)
        self.assertEqual(compute_trigger_retry_countdown(1), 60)
        self.assertEqual(compute_trigger_retry_countdown(2), 120)
        self.assertEqual(compute_trigger_retry_countdown(10), 900)

    def test_schedule_trigger_task_retry_calls_retry_before_max(self):
        retry_error = RuntimeError("retry queued")
        dummy_task = SimpleNamespace(
            request=SimpleNamespace(retries=2),
            retry=Mock(side_effect=retry_error),
        )

        with self.assertLogs("nova.tasks.tasks", level="WARNING") as logs:
            with self.assertRaisesMessage(RuntimeError, "retry queued"):
                schedule_trigger_task_retry(
                    dummy_task,
                    RuntimeError("boom"),
                    task_definition_id=123,
                    runner_name="cron",
                )

        dummy_task.retry.assert_called_once()
        kwargs = dummy_task.retry.call_args.kwargs
        self.assertEqual(kwargs["countdown"], compute_trigger_retry_countdown(2))
        self.assertEqual(kwargs["max_retries"], TRIGGER_TASK_MAX_RETRIES)
        self.assertTrue(any("Retrying task definition 123 (cron)" in line for line in logs.output))

    def test_schedule_trigger_task_retry_returns_false_when_exhausted(self):
        dummy_task = SimpleNamespace(
            request=SimpleNamespace(retries=TRIGGER_TASK_MAX_RETRIES),
            retry=Mock(),
        )

        with self.assertLogs("nova.tasks.tasks", level="ERROR") as logs:
            should_retry = schedule_trigger_task_retry(
                dummy_task,
                RuntimeError("boom"),
                task_definition_id=456,
                runner_name="email_poll",
            )

        self.assertFalse(should_retry)
        dummy_task.retry.assert_not_called()
        self.assertTrue(any("Task definition 456 (email_poll) reached max retries" in line for line in logs.output))

    @patch("nova.tasks.tasks.execute_agent_task_definition")
    def test_run_task_definition_cron_success_updates_status(self, mocked_execute):
        task_def = self._create_agent_cron_task(name="cron-success")
        mocked_execute.return_value = {
            "task_id": 1,
            "thread_id": 2,
            "message_id": 3,
        }

        result = run_task_definition_cron.run(task_def.id)

        self.assertEqual(result["status"], "ok")
        mocked_execute.assert_called_once()
        task_def.refresh_from_db()
        self.assertIsNotNone(task_def.last_run_at)
        self.assertIsNone(task_def.last_error)

    @patch("nova.tasks.tasks.schedule_trigger_task_retry")
    @patch("nova.tasks.tasks.execute_agent_task_definition")
    def test_run_task_definition_cron_uses_retry_policy_on_failure(self, mocked_execute, mocked_retry_policy):
        task_def = self._create_agent_cron_task(name="cron-failure")
        mocked_execute.side_effect = RuntimeError("execution failed")
        mocked_retry_policy.side_effect = RuntimeError("retry queued")

        with self.assertLogs("nova.tasks.tasks", level="ERROR") as logs:
            with self.assertRaisesMessage(RuntimeError, "retry queued"):
                run_task_definition_cron.run(task_def.id)

        mocked_retry_policy.assert_called_once()
        task_def.refresh_from_db()
        self.assertIn("execution failed", task_def.last_error or "")
        self.assertTrue(any("Error executing task definition" in line for line in logs.output))

    @patch("nova.tasks.tasks.poll_new_unseen_email_headers")
    def test_poll_task_definition_email_noop_when_no_new_email(self, mocked_poll):
        task_def = self._create_agent_email_task(name="email-noop")
        mocked_poll.return_value = {
            "headers": [],
            "state": {"last_uid": 10},
            "skip_reason": None,
        }

        result = poll_task_definition_email.run(task_def.id)

        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["new_email_count"], 0)
        task_def.refresh_from_db()
        self.assertEqual(task_def.runtime_state.get("last_uid"), 10)

    @patch("nova.tasks.tasks.execute_agent_task_definition")
    @patch("nova.tasks.tasks.poll_new_unseen_email_headers")
    def test_poll_task_definition_email_runs_agent_when_new_email(self, mocked_poll, mocked_execute):
        task_def = self._create_agent_email_task(name="email-ok")
        mocked_poll.return_value = {
            "headers": [
                {
                    "uid": 42,
                    "from": "alice@example.com",
                    "subject": "Hello",
                    "date": "2026-02-08T10:00:00+00:00",
                }
            ],
            "state": {"last_uid": 42},
            "skip_reason": None,
        }
        mocked_execute.return_value = {
            "task_id": 11,
            "thread_id": 12,
            "message_id": 13,
        }

        result = poll_task_definition_email.run(task_def.id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["new_email_count"], 1)
        mocked_execute.assert_called_once()
        task_def.refresh_from_db()
        self.assertEqual(task_def.runtime_state.get("last_uid"), 42)
        self.assertIsNotNone(task_def.last_run_at)

    @patch("nova.tasks.tasks.schedule_trigger_task_retry")
    @patch("nova.tasks.tasks.poll_new_unseen_email_headers")
    def test_poll_task_definition_email_uses_retry_policy_on_failure(self, mocked_poll, mocked_retry_policy):
        task_def = self._create_agent_email_task(name="email-failure")
        mocked_poll.side_effect = RuntimeError("imap unavailable")
        mocked_retry_policy.side_effect = RuntimeError("retry queued")

        with self.assertLogs("nova.tasks.tasks", level="ERROR") as logs:
            with self.assertRaisesMessage(RuntimeError, "retry queued"):
                poll_task_definition_email.run(task_def.id)

        mocked_retry_policy.assert_called_once()
        task_def.refresh_from_db()
        self.assertIn("imap unavailable", task_def.last_error or "")
        self.assertTrue(any("Error polling task definition" in line for line in logs.output))

    @patch("nova.tasks.tasks.current_app.tasks.get")
    def test_run_task_definition_maintenance_dispatches_configured_task(self, mocked_get_task):
        mocked_task_impl = Mock()
        mocked_get_task.return_value = mocked_task_impl
        task_def = self._create_maintenance_task(name="maintenance-dispatch", maintenance_task="fake.maintenance")

        result = run_task_definition_maintenance.run(task_def.id)

        self.assertEqual(result["status"], "ok")
        mocked_get_task.assert_called_once_with("fake.maintenance")
        mocked_task_impl.delay.assert_called_once_with(user_id=self.user.id)
