from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.models.CheckpointLink import CheckpointLink
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
