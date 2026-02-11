from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from nova.models.Message import Actor
from nova.tasks.tasks import (
    AgentTaskExecutor,
    ContextConsumptionTracker,
    SummarizationTaskExecutor,
    delete_checkpoints,
    generate_thread_title_task,
    resume_ai_task_celery,
    run_ai_task_celery,
    summarize_thread_task,
)


class ContextConsumptionTrackerTests(IsolatedAsyncioTestCase):
    async def test_calculate_uses_real_tokens_when_available(self):
        checkpoint = SimpleNamespace(
            checkpoint={
                "channel_values": {
                    "messages": [SimpleNamespace(usage_metadata={"total_tokens": 321})],
                }
            }
        )
        checkpointer = AsyncMock()
        checkpointer.aget_tuple.return_value = checkpoint
        checkpointer.conn.close = AsyncMock()
        agent = SimpleNamespace(config={"configurable": {"thread_id": "t1"}})
        agent_config = SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=4096))

        with patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock, return_value=checkpointer):
            real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(agent_config, agent)

        self.assertEqual(real_tokens, 321)
        self.assertIsNone(approx_tokens)
        self.assertEqual(max_context, 4096)
        checkpointer.conn.close.assert_awaited_once()

    async def test_calculate_falls_back_to_approximation(self):
        memory = [
            HumanMessage(content="hello"),
            AIMessage(content=["abc", {"x": 1}]),
        ]
        checkpoint = SimpleNamespace(checkpoint={"channel_values": {"messages": memory}})
        checkpointer = AsyncMock()
        checkpointer.aget_tuple.return_value = checkpoint
        checkpointer.conn.close = AsyncMock()
        agent = SimpleNamespace(config={"configurable": {"thread_id": "t2"}})
        agent_config = SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=2048))

        with patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock, return_value=checkpointer):
            real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(agent_config, agent)

        self.assertIsNone(real_tokens)
        self.assertGreater(approx_tokens, 0)
        self.assertEqual(max_context, 2048)
        checkpointer.conn.close.assert_awaited_once()

    def test_approximate_tokens_handles_mixed_content_types(self):
        memory = [
            HumanMessage(content="hello world"),
            AIMessage(content=["a", {"n": 123}, {"k": "v"}]),
            SimpleNamespace(content="ignored"),
        ]
        tokens = ContextConsumptionTracker._approximate_tokens(memory)
        self.assertGreater(tokens, 1)


class AgentTaskExecutorUnitTests(IsolatedAsyncioTestCase):
    async def test_enqueue_thread_title_generation_only_for_default_titles(self):
        task = SimpleNamespace(id=1, progress_logs=[], save=Mock())
        thread = SimpleNamespace(id=42, subject="New thread 42")
        agent_config = SimpleNamespace(id=9, llm_provider=SimpleNamespace(max_context_tokens=1000))
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=agent_config,
            prompt="hello",
        )

        with patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay:
            await executor._enqueue_thread_title_generation()
        mocked_delay.assert_called_once_with(
            thread_id=42,
            user_id=1,
            agent_config_id=9,
            source_task_id=1,
        )

        thread.subject = "Custom subject"
        with patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay:
            await executor._enqueue_thread_title_generation()
        mocked_delay.assert_not_called()

    async def test_process_result_updates_message_and_context_info(self):
        task = SimpleNamespace(id=1, progress_logs=[], save=Mock(), result=None)
        message = SimpleNamespace(internal_data={}, save=Mock())
        thread = SimpleNamespace(subject="thread n°1", add_message=Mock(return_value=message), save=Mock())
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=1000)),
            prompt="prompt",
        )
        executor.handler = SimpleNamespace(on_context_consumption=AsyncMock())
        executor.llm = SimpleNamespace(ainvoke=AsyncMock(return_value="Title"))

        with (
            patch("nova.tasks.tasks.ContextConsumptionTracker.calculate", new_callable=AsyncMock, return_value=(50, None, 1000)),
            patch.object(executor, "_enqueue_thread_title_generation", new_callable=AsyncMock) as mocked_enqueue_title,
        ):
            await executor._process_result("Agent answer")

        self.assertEqual(task.result, "Agent answer")
        thread.add_message.assert_called_once_with("Agent answer", actor=Actor.AGENT)
        self.assertEqual(message.internal_data["real_tokens"], 50)
        executor.handler.on_context_consumption.assert_awaited_once_with(50, None, 1000)
        mocked_enqueue_title.assert_awaited_once()


class GenerateThreadTitleTaskTests(SimpleTestCase):
    @patch("nova.tasks.tasks.Thread.objects.filter")
    @patch("nova.tasks.tasks.AgentConfig.objects.select_related")
    @patch("nova.tasks.tasks.Message.objects.filter")
    @patch("nova.tasks.tasks.Thread.objects.select_related")
    @patch("nova.tasks.tasks.create_provider_llm")
    def test_generate_thread_title_updates_default_subject_and_publishes(
        self,
        mocked_create_provider_llm,
        mocked_thread_select_related,
        mocked_message_filter,
        mocked_agent_select_related,
        mocked_thread_filter,
    ):
        user = SimpleNamespace(id=7)
        thread = SimpleNamespace(id=11, user=user, subject="New thread 3")
        mocked_thread_select_related.return_value.get.return_value = thread

        mocked_message_filter.return_value.order_by.return_value.__getitem__.return_value = [
            SimpleNamespace(actor=Actor.USER, text="Need a travel plan"),
            SimpleNamespace(actor=Actor.AGENT, text="Sure, where and when?"),
        ]

        provider = SimpleNamespace()
        agent_config = SimpleNamespace(llm_provider=provider)
        mocked_agent_select_related.return_value.get.return_value = agent_config

        fake_llm = AsyncMock()
        fake_llm.ainvoke.return_value = SimpleNamespace(content="[THINK]internal[/THINK]\nTrip planning")
        mocked_create_provider_llm.return_value = fake_llm

        mocked_thread_filter.return_value.update.return_value = 1

        with (
            patch("nova.tasks.tasks._build_langfuse_invoke_config", return_value=({}, None)),
            patch("nova.tasks.tasks._publish_thread_subject_update") as mocked_publish,
        ):
            result = generate_thread_title_task.run(
                thread_id=11,
                user_id=7,
                agent_config_id=13,
                source_task_id=19,
            )

        self.assertEqual(result["status"], "ok")
        mocked_thread_filter.assert_called_once_with(id=11, user_id=7, subject="New thread 3")
        mocked_publish.assert_called_once_with(19, 11, "Trip planning")

    @patch("nova.tasks.tasks.Thread.objects.select_related")
    def test_generate_thread_title_skips_when_subject_not_default(self, mocked_thread_select_related):
        user = SimpleNamespace(id=7)
        thread = SimpleNamespace(id=11, user=user, subject="Custom title")
        mocked_thread_select_related.return_value.get.return_value = thread

        result = generate_thread_title_task.run(
            thread_id=11,
            user_id=7,
            agent_config_id=13,
            source_task_id=19,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "subject_already_customized")


class CeleryEntryPointTests(SimpleTestCase):
    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.AgentTaskExecutor")
    @patch("nova.tasks.tasks.Message.objects.select_related")
    @patch("nova.tasks.tasks.AgentConfig.objects.select_related")
    @patch("nova.tasks.tasks.Thread.objects.select_related")
    @patch("nova.tasks.tasks.User.objects.get")
    @patch("nova.tasks.tasks.Task.objects.select_related")
    def test_run_ai_task_celery_success(
        self,
        mocked_task_select_related,
        mocked_user_get,
        mocked_thread_select_related,
        mocked_agent_select_related,
        mocked_message_select_related,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        task = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        thread = SimpleNamespace(id=3)
        agent = SimpleNamespace(id=4)
        message = SimpleNamespace(id=5, text="hello")

        mocked_task_select_related.return_value.get.return_value = task
        mocked_user_get.return_value = user
        mocked_thread_select_related.return_value.get.return_value = thread
        mocked_agent_select_related.return_value.get.return_value = agent
        mocked_message_select_related.return_value.get.return_value = message
        executor = SimpleNamespace(execute_or_resume=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        run_ai_task_celery.run(1, 2, 3, 4, 5)

        mocked_executor_cls.assert_called_once_with(task, user, thread, agent, "hello", source_message_id=5)
        mocked_asyncio_run.assert_called_once()
        executor.execute_or_resume.assert_called_once()

    @patch.object(run_ai_task_celery, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Task.objects.select_related")
    def test_run_ai_task_celery_retries_on_failure(self, mocked_task_select_related, mocked_retry):
        mocked_task_select_related.return_value.get.side_effect = RuntimeError("db down")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            run_ai_task_celery.run(1, 2, 3, 4, 5)

        mocked_retry.assert_called_once()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.AgentTaskExecutor")
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_success(
        self,
        mocked_interaction_select_related,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        task = SimpleNamespace(user=SimpleNamespace(id=1))
        thread = SimpleNamespace(id=3)
        agent_config = SimpleNamespace(id=4)
        interaction = SimpleNamespace(
            id=9,
            task=task,
            thread=thread,
            agent_config=agent_config,
            answer="yes",
            status="answered",
        )
        mocked_interaction_select_related.return_value.get.return_value = interaction
        executor = SimpleNamespace(execute_or_resume=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        resume_ai_task_celery.run(9)

        mocked_executor_cls.assert_called_once_with(task, task.user, thread, agent_config, interaction)
        executor.execute_or_resume.assert_called_once()
        mocked_asyncio_run.assert_called_once()

    @patch.object(resume_ai_task_celery, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_retries_on_failure(self, mocked_interaction_select_related, mocked_retry):
        mocked_interaction_select_related.return_value.get.side_effect = RuntimeError("missing")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            resume_ai_task_celery.run(99)

        mocked_retry.assert_called_once()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.SummarizationTaskExecutor")
    @patch("nova.tasks.tasks.Task.objects.get")
    @patch("nova.tasks.tasks.AgentConfig.objects.get")
    @patch("nova.tasks.tasks.User.objects.get")
    @patch("nova.tasks.tasks.Thread.objects.get")
    def test_summarize_thread_task_success(
        self,
        mocked_thread_get,
        mocked_user_get,
        mocked_agent_get,
        mocked_task_get,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        thread = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        agent = SimpleNamespace(id=3)
        task = SimpleNamespace(id=4)
        mocked_thread_get.return_value = thread
        mocked_user_get.return_value = user
        mocked_agent_get.return_value = agent
        mocked_task_get.return_value = task
        executor = SimpleNamespace(execute=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        summarize_thread_task.run(1, 2, 3, 4, include_sub_agents=True, sub_agent_ids=[10, 11])

        mocked_executor_cls.assert_called_once_with(task, user, thread, agent, True, [10, 11])
        executor.execute.assert_called_once()
        mocked_asyncio_run.assert_called_once()

    @patch.object(summarize_thread_task, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Thread.objects.get")
    def test_summarize_thread_task_retries_on_failure(self, mocked_thread_get, mocked_retry):
        mocked_thread_get.side_effect = RuntimeError("missing thread")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            summarize_thread_task.run(1, 2, 3, 4)

        mocked_retry.assert_called_once()


class SummarizationTaskExecutorTests(IsolatedAsyncioTestCase):
    async def test_perform_summarization_with_subagents(self):
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
            include_sub_agents=True,
            sub_agent_ids=[200, 201],
        )
        sub_a = SimpleNamespace(id=200, name="sub-a")
        sub_b = SimpleNamespace(id=201, name="sub-b")

        with (
            patch.object(executor, "_summarize_single_agent", new_callable=AsyncMock) as mocked_single,
            patch("nova.tasks.tasks.AgentConfig.objects.get", side_effect=[sub_a, sub_b]),
        ):
            await executor._perform_summarization()

        self.assertEqual(mocked_single.await_count, 3)
        first_call_agent = mocked_single.await_args_list[0].args[0]
        self.assertEqual(first_call_agent.id, 100)

    @patch("nova.llm.llm_agent.LLMAgent.create", new_callable=AsyncMock)
    async def test_summarize_single_agent_raises_when_middleware_missing(self, mocked_create_agent):
        fake_agent = SimpleNamespace(middleware=[], cleanup=AsyncMock())
        mocked_create_agent.return_value = fake_agent
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
        )

        with self.assertRaisesRegex(ValueError, "SummarizationMiddleware not found"):
            await executor._summarize_single_agent(SimpleNamespace(name="sub"))

        fake_agent.cleanup.assert_awaited_once()

    @patch("nova.llm.llm_agent.LLMAgent.create", new_callable=AsyncMock)
    async def test_summarize_single_agent_raises_on_failed_summary(self, mocked_create_agent):
        middleware = SimpleNamespace(manual_summarize=AsyncMock(return_value={"status": "error", "message": "boom"}))
        fake_agent = SimpleNamespace(middleware=[middleware], cleanup=AsyncMock())
        mocked_create_agent.return_value = fake_agent
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
        )

        with self.assertRaisesRegex(ValueError, "Summarization failed"):
            await executor._summarize_single_agent(SimpleNamespace(name="main"))

        fake_agent.cleanup.assert_awaited_once()

    @patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock)
    async def test_delete_checkpoints_always_closes_connection(self, mocked_get_checkpointer):
        checkpointer = AsyncMock()
        checkpointer.conn.close = AsyncMock()
        mocked_get_checkpointer.return_value = checkpointer

        await delete_checkpoints("ckp-123")

        checkpointer.adelete_thread.assert_awaited_once_with("ckp-123")
        checkpointer.conn.close.assert_awaited_once()
