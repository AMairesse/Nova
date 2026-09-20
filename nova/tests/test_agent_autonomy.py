from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase

from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.runtime.system_prompt import build_runtime_system_prompt
from nova.tasks.tasks import ReactTerminalTaskExecutor
from nova.runtime.agent import ReactTerminalRunResult
from nova.tests.factories import create_agent, create_provider, create_user


class AgentAutonomyTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="autonomy")
        self.provider = create_provider(self.user, name="autonomy-provider")
        self.agent = create_agent(self.user, self.provider, name="autonomy-agent")

    def test_agent_instructions_use_a_single_prompt_field(self):
        from user_settings.forms import AgentForm

        form = AgentForm(instance=self.agent, user=self.user)
        self.assertNotIn("autonomy_instructions", form.fields)
        prompt = build_runtime_system_prompt(
            capabilities=Mock(),
            tools_enabled=False,
            agent_instructions="You may decide routine formatting without asking.",
        )
        self.assertNotIn("User-defined autonomy instructions:", prompt)
        self.assertIn("routine formatting", prompt)

    def test_no_change_routine_does_not_create_message_or_push(self):
        thread = Thread.objects.create(user=self.user, subject="routine")
        task = Task.objects.create(
            user=self.user, thread=thread, agent_config=self.agent,
            status=TaskStatus.RUNNING,
        )
        RoutineRun.objects.create(
            user=self.user, name="routine", key="autonomy-no-change", task=task,
        )
        executor = ReactTerminalTaskExecutor(task, self.user, thread, self.agent, "prompt")
        executor.handler.record_progress = AsyncMock()
        executor.handler.push_notifications_enabled = True
        executor.thread.add_message = Mock(wraps=thread.add_message)

        with patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=None):
            async_to_sync(executor._process_result)(
                ReactTerminalRunResult(
                    final_answer="NOVA_NO_CHANGE",
                    real_tokens=None,
                    approx_tokens=None,
                    max_context=None,
                )
            )

        self.assertEqual(task.result, "NOVA_NO_CHANGE")
        self.assertFalse(executor.handler.push_notifications_enabled)
        executor.thread.add_message.assert_not_called()

    def test_no_change_ordinary_task_keeps_message_and_push(self):
        thread = Thread.objects.create(user=self.user, subject="ordinary")
        task = Task.objects.create(
            user=self.user, thread=thread, agent_config=self.agent,
            status=TaskStatus.RUNNING,
        )
        executor = ReactTerminalTaskExecutor(task, self.user, thread, self.agent, "prompt")
        executor.handler.record_progress = AsyncMock()
        executor.handler.on_context_consumption = AsyncMock()
        executor.handler.on_new_message = AsyncMock()
        executor._build_realtime_message_payload = AsyncMock(return_value={})
        executor._enqueue_thread_title_generation = AsyncMock()
        executor.thread.add_message = Mock(wraps=thread.add_message)

        async_to_sync(executor._process_result)(
            ReactTerminalRunResult(
                final_answer="NOVA_NO_CHANGE",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )
        )

        self.assertTrue(executor.handler.push_notifications_enabled)
        executor.thread.add_message.assert_called_once()
