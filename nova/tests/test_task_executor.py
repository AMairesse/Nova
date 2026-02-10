from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.models.CheckpointLink import CheckpointLink
from nova.models.Interaction import Interaction
from nova.models.Message import MessageType
from nova.models.Provider import ProviderType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tasks.TaskExecutor import TaskErrorCategory, TaskExecutor
from nova.tests.factories import create_agent, create_provider, create_user


class TaskExecutorTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="executor-user", email="executor@example.com")
        self.provider = create_provider(
            self.user,
            provider_type=ProviderType.OLLAMA,
            name="executor-provider",
            model="llama3.2",
        )
        self.agent = create_agent(self.user, self.provider, name="executor-agent")
        self.thread = Thread.objects.create(user=self.user, subject="thread n°1", mode=Thread.Mode.THREAD)
        self.task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )

    def _make_executor(self, *, thread=None):
        executor = TaskExecutor(
            task=self.task,
            user=self.user,
            thread=thread or self.thread,
            agent_config=self.agent,
            prompt="hello",
            source_message_id=None,
        )
        executor.handler = AsyncMock()
        return executor

    def test_execute_or_resume_regular_result_flow(self):
        executor = self._make_executor()
        executor._initialize_task = AsyncMock()
        executor._create_llm_agent = AsyncMock()
        executor._create_prompt = AsyncMock(return_value="hello")
        executor._run_agent = AsyncMock(return_value="final answer")
        executor._process_result = AsyncMock()
        executor._finalize_task = AsyncMock()
        executor._cleanup = AsyncMock()

        asyncio.run(executor.execute_or_resume())

        executor._initialize_task.assert_awaited_once()
        executor._create_llm_agent.assert_awaited_once()
        executor._run_agent.assert_awaited_once()
        executor._process_result.assert_awaited_once_with("final answer")
        executor._finalize_task.assert_awaited_once()
        executor._cleanup.assert_awaited_once()

    def test_execute_or_resume_interrupt_flow(self):
        executor = self._make_executor()
        interrupt_payload = {"__interrupt__": [object()]}
        executor._initialize_task = AsyncMock()
        executor._create_llm_agent = AsyncMock()
        executor._create_prompt = AsyncMock(return_value="hello")
        executor._run_agent = AsyncMock(return_value=interrupt_payload)
        executor._process_interuption = AsyncMock()
        executor._cleanup = AsyncMock()

        asyncio.run(executor.execute_or_resume())

        executor._process_interuption.assert_awaited_once_with(interrupt_payload)
        executor._cleanup.assert_awaited_once()

    def test_execute_or_resume_with_resume_payload_calls_llm_resume(self):
        executor = self._make_executor()
        resumed = {"action": "user_response", "user_response": "yes"}

        async def fake_create_llm():
            executor.llm = AsyncMock()
            executor.llm.aresume.return_value = "done"

        executor._initialize_task = AsyncMock()
        executor._create_llm_agent = AsyncMock(side_effect=fake_create_llm)
        executor._process_result = AsyncMock()
        executor._finalize_task = AsyncMock()
        executor._cleanup = AsyncMock()

        asyncio.run(executor.execute_or_resume(interruption_response=resumed))

        executor.handler.on_resume_task.assert_awaited_once_with(resumed)
        executor.llm.aresume.assert_awaited_once()
        executor._process_result.assert_awaited_once_with("done")
        executor._finalize_task.assert_awaited_once()
        executor._cleanup.assert_awaited_once()

    def test_execute_or_resume_routes_exceptions_to_handler(self):
        executor = self._make_executor()
        executor._initialize_task = AsyncMock(side_effect=RuntimeError("boom"))
        executor._handle_execution_error = AsyncMock()
        executor._cleanup = AsyncMock()

        asyncio.run(executor.execute_or_resume())

        executor._handle_execution_error.assert_awaited_once()
        executor._cleanup.assert_awaited_once()

    def test_categorize_error_variants(self):
        executor = self._make_executor()
        self.assertEqual(executor._categorize_error(RuntimeError("tool crashed")), TaskErrorCategory.TOOL_FAILURE)
        self.assertEqual(executor._categorize_error(RuntimeError("agent failed")), TaskErrorCategory.AGENT_FAILURE)
        self.assertEqual(executor._categorize_error(RuntimeError("network timeout")), TaskErrorCategory.NETWORK_ERROR)
        self.assertEqual(executor._categorize_error(RuntimeError("validation issue")), TaskErrorCategory.VALIDATION_ERROR)
        self.assertEqual(executor._categorize_error(RuntimeError("other")), TaskErrorCategory.SYSTEM_ERROR)

    @patch("nova.llm.checkpoints.get_checkpointer")
    def test_purge_continuous_subagent_checkpoints_keeps_main(self, mocked_get_checkpointer):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        main_agent = create_agent(self.user, self.provider, name="main-agent")
        sub_agent = create_agent(self.user, self.provider, name="sub-agent")
        keep_link = CheckpointLink.objects.create(thread=continuous_thread, agent=main_agent)
        purge_link = CheckpointLink.objects.create(thread=continuous_thread, agent=sub_agent)

        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )
        executor = TaskExecutor(
            task=task,
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            prompt="hello",
            source_message_id=None,
        )

        fake_checkpointer = AsyncMock()
        fake_checkpointer.conn.close = AsyncMock()
        mocked_get_checkpointer.return_value = fake_checkpointer

        asyncio.run(executor._purge_continuous_subagent_checkpoints())

        fake_checkpointer.adelete_thread.assert_awaited_once_with(purge_link.checkpoint_id)
        fake_checkpointer.conn.close.assert_awaited_once()
        self.assertNotEqual(keep_link.checkpoint_id, purge_link.checkpoint_id)

    def test_create_prompt_returns_current_prompt(self):
        executor = self._make_executor()
        executor.prompt = "custom prompt"

        result = asyncio.run(executor._create_prompt())

        self.assertEqual(result, "custom prompt")

    def test_initialize_task_sets_running_and_logs(self):
        executor = self._make_executor()

        asyncio.run(executor._initialize_task())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.RUNNING)
        self.assertEqual(self.task.progress_logs[-1]["step"], "Initializing AI task")

        asyncio.run(executor._initialize_task(interruption_response={"action": "resume"}))
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress_logs[-1]["step"], "Resuming after user input")

    @patch("nova.tasks.TaskExecutor.LLMAgent.create", new_callable=AsyncMock)
    def test_create_llm_agent_populates_resources(self, mocked_create):
        executor = self._make_executor()
        fake_llm = AsyncMock()
        fake_llm._resources = {}
        mocked_create.return_value = fake_llm

        asyncio.run(executor._create_llm_agent())

        self.assertEqual(executor.llm._resources["task_id"], self.task.id)
        self.assertIsNotNone(executor.llm._resources["channel_layer"])

    @patch("nova.tasks.TaskExecutor.LLMAgent.create", new_callable=AsyncMock)
    def test_create_llm_agent_ignores_resource_injection_failures(self, mocked_create):
        executor = self._make_executor()
        fake_llm = AsyncMock()
        fake_llm._resources = None
        mocked_create.return_value = fake_llm

        asyncio.run(executor._create_llm_agent())

        self.assertIs(executor.llm, fake_llm)

    def test_create_interaction_persists_question_message(self):
        executor = self._make_executor()

        interaction = asyncio.run(
            executor._create_interaction(
                question="Need confirmation?",
                schema={"type": "object"},
                agent_name="Planner",
            )
        )

        self.assertEqual(interaction.question, "Need confirmation?")
        self.assertEqual(interaction.origin_name, "Planner")
        self.assertTrue(hasattr(interaction, "question_message"))
        self.assertEqual(interaction.question_message.message_type, MessageType.INTERACTION_QUESTION)
        self.assertEqual(interaction.question_message.interaction_id, interaction.id)
        self.assertEqual(Interaction.objects.count(), 1)

    def test_process_interruption_unsupported_action_raises(self):
        executor = self._make_executor()
        payload = {"__interrupt__": [type("I", (), {"value": {"action": "noop"}})()]}

        with self.assertRaisesMessage(Exception, "Unsupported interruption action: noop"):
            asyncio.run(executor._process_interuption(payload))

    def test_process_interruption_marks_task_awaiting_input(self):
        executor = self._make_executor()
        interaction = SimpleNamespace(id=99)
        executor._create_interaction = AsyncMock(return_value=interaction)
        payload = {
            "__interrupt__": [
                type(
                    "I",
                    (),
                    {"value": {"action": "ask_user", "question": "Continue?", "schema": {"type": "object"}, "agent_name": "Planner"}},
                )()
            ]
        }

        asyncio.run(executor._process_interuption(payload))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.AWAITING_INPUT)
        executor.handler.on_interrupt.assert_awaited_once_with(99, "Continue?", {"type": "object"}, "Planner")

    @patch("nova.continuous.checkpoint_state.ensure_continuous_checkpoint_state", new_callable=AsyncMock)
    def test_run_agent_rebuilds_continuous_checkpoint_when_needed(self, mocked_rebuild):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=self.agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )
        executor = TaskExecutor(
            task=task,
            user=self.user,
            thread=continuous_thread,
            agent_config=self.agent,
            prompt="hello",
            source_message_id=123,
        )
        executor.handler = AsyncMock()
        executor.llm = AsyncMock()
        executor.llm.ainvoke.return_value = "done"
        mocked_rebuild.return_value = True

        result = asyncio.run(executor._run_agent())

        self.assertEqual(result, "done")
        self.assertTrue(any(log["step"] == "Continuous context: checkpoint rebuilt" for log in executor.task.progress_logs))
        mocked_rebuild.assert_awaited_once()

    @patch("nova.continuous.checkpoint_state.ensure_continuous_checkpoint_state", new_callable=AsyncMock)
    def test_run_agent_ignores_checkpoint_rebuild_errors(self, mocked_rebuild):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=self.agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )
        executor = TaskExecutor(
            task=task,
            user=self.user,
            thread=continuous_thread,
            agent_config=self.agent,
            prompt="hello",
            source_message_id=None,
        )
        executor.handler = AsyncMock()
        executor.llm = AsyncMock()
        executor.llm.ainvoke.return_value = "done"
        mocked_rebuild.side_effect = RuntimeError("rebuild failed")

        result = asyncio.run(executor._run_agent())

        self.assertEqual(result, "done")
        executor.llm.ainvoke.assert_awaited_once_with("hello")

    def test_finalize_task_marks_completed_and_publishes(self):
        executor = self._make_executor()
        self.task.result = "final text"
        self.task.save(update_fields=["result", "updated_at"])

        asyncio.run(executor._finalize_task())

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)
        executor.handler.on_task_complete.assert_awaited_once_with("final text", self.thread.id, self.thread.subject)

    def test_handle_execution_error_sets_failed_state_even_if_publish_fails(self):
        executor = self._make_executor()
        executor.handler.on_error.side_effect = RuntimeError("ws down")

        asyncio.run(executor._handle_execution_error(RuntimeError("network timeout")))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.FAILED)
        self.assertIn("network_error", self.task.result)

    def test_cleanup_logs_when_llm_cleanup_fails(self):
        executor = self._make_executor()
        executor.llm = AsyncMock()
        executor.llm.cleanup.side_effect = RuntimeError("cleanup fail")

        with self.assertLogs("nova.tasks.TaskExecutor", level="ERROR") as logs:
            asyncio.run(executor._cleanup())

        self.assertTrue(any("Failed to cleanup LLM" in line for line in logs.output))

    def test_purge_continuous_subagent_checkpoints_returns_without_context(self):
        executor = TaskExecutor(
            task=self.task,
            user=self.user,
            thread=None,
            agent_config=self.agent,
            prompt="hello",
            source_message_id=None,
        )
        executor.handler = AsyncMock()

        asyncio.run(executor._purge_continuous_subagent_checkpoints())

    @patch("nova.llm.checkpoints.get_checkpointer")
    def test_purge_continuous_subagent_checkpoints_skips_when_no_purge_ids(self, mocked_get_checkpointer):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        main_agent = create_agent(self.user, self.provider, name="main-only-agent")
        CheckpointLink.objects.create(thread=continuous_thread, agent=main_agent)
        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )
        executor = TaskExecutor(
            task=task,
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            prompt="hello",
            source_message_id=None,
        )

        asyncio.run(executor._purge_continuous_subagent_checkpoints())

        mocked_get_checkpointer.assert_not_called()

    @patch("nova.llm.checkpoints.get_checkpointer")
    def test_purge_continuous_subagent_checkpoints_ignores_per_checkpoint_failures(self, mocked_get_checkpointer):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        main_agent = create_agent(self.user, self.provider, name="main-a")
        sub_agent_1 = create_agent(self.user, self.provider, name="sub-a")
        sub_agent_2 = create_agent(self.user, self.provider, name="sub-b")
        CheckpointLink.objects.create(thread=continuous_thread, agent=main_agent)
        p1 = CheckpointLink.objects.create(thread=continuous_thread, agent=sub_agent_1)
        p2 = CheckpointLink.objects.create(thread=continuous_thread, agent=sub_agent_2)
        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            status=TaskStatus.PENDING,
            progress_logs=[],
        )
        executor = TaskExecutor(
            task=task,
            user=self.user,
            thread=continuous_thread,
            agent_config=main_agent,
            prompt="hello",
            source_message_id=None,
        )
        fake_checkpointer = AsyncMock()
        fake_checkpointer.conn.close = AsyncMock()
        fake_checkpointer.adelete_thread.side_effect = [RuntimeError("broken"), None]
        mocked_get_checkpointer.return_value = fake_checkpointer

        asyncio.run(executor._purge_continuous_subagent_checkpoints())

        self.assertEqual(fake_checkpointer.adelete_thread.await_count, 2)
        fake_checkpointer.adelete_thread.assert_any_await(p1.checkpoint_id)
        fake_checkpointer.adelete_thread.assert_any_await(p2.checkpoint_id)
        fake_checkpointer.conn.close.assert_awaited_once()
