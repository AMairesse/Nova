# nova/tests/test_summarization_middleware.py
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage

import nova.llm.llm_agent as llm_agent_mod
from nova.llm.summarization_middleware import SummarizationMiddleware, SummarizerAgent, TokenCounter
from nova.llm.agent_middleware import AgentContext
from nova.tests.base import BaseTestCase


class SummarizationMiddlewareTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.agent_config = MagicMock()
        self.agent_config.auto_summarize = True
        self.agent_config.token_threshold = 100
        self.agent_config.preserve_recent = 2
        self.agent_config.strategy = 'conversation'
        self.agent_config.max_summary_length = 500
        self.agent_config.summary_model = None
        self.agent = MagicMock()
        self.middleware = SummarizationMiddleware(self.agent_config, self.agent)

    def test_should_summarize_disabled(self):
        """Test that summarization is not triggered when disabled."""
        self.agent_config.auto_summarize = False
        context = AgentContext(agent_config=MagicMock(), user=self.user, thread=MagicMock())

        # Mock token counter to return high count
        with patch.object(TokenCounter, 'count_context_tokens', return_value=200):
            result = asyncio.run(self.middleware._should_summarize(context))
            self.assertFalse(result)

    def test_should_summarize_enabled_below_threshold(self):
        """Test that summarization is not triggered when below threshold."""
        context = AgentContext(agent_config=MagicMock(), user=self.user, thread=MagicMock())

        # Mock agent to have max_context_tokens
        self.agent.agent_config.llm_provider.max_context_tokens = 1000

        # Mock token counter to return count below threshold
        with patch.object(TokenCounter, 'count_context_tokens', return_value=50):
            result = asyncio.run(self.middleware._should_summarize(context))
            self.assertFalse(result)

    def test_should_summarize_enabled_above_threshold(self):
        """Test that summarization is triggered when above threshold."""
        context = AgentContext(agent_config=MagicMock(), user=self.user, thread=MagicMock())

        # Mock agent to have max_context_tokens
        self.agent.agent_config.llm_provider.max_context_tokens = 1000

        # Mock token counter to return count above threshold
        with patch.object(TokenCounter, 'count_context_tokens', return_value=150):
            result = asyncio.run(self.middleware._should_summarize(context))
            self.assertTrue(result)

    async def test_inject_summary_into_checkpoint(self):
        """Test that checkpoint injection creates new checkpoint with summarized messages."""
        # Setup mocks
        mock_checkpointer = AsyncMock()

        # Create mock checkpoint
        mock_checkpoint = MagicMock()
        mock_checkpoint.checkpoint = {
            'channel_values': {
                'messages': [
                    HumanMessage(content="Old message 1"),
                    AIMessage(content="Old response 1"),
                    HumanMessage(content="Recent message"),
                    AIMessage(content="Recent response")
                ]
            }
        }
        mock_checkpoint.config = {
            'configurable': {
                'thread_id': 'test-thread',
                'checkpoint_ns': ''
            }
        }
        mock_checkpoint.metadata = {'test': 'metadata'}

        # Test data
        summary = "Summary of old messages"
        preserved_messages = [
            HumanMessage(content="Recent message"),
            AIMessage(content="Recent response")
        ]

        # Mock the agent's langchain_agent (the graph)
        mock_graph = AsyncMock()
        self.agent.langchain_agent = mock_graph

        # Call the method
        await self.middleware._inject_summary_into_checkpoint(
            summary, preserved_messages, mock_checkpoint, mock_checkpointer
        )

        # Verify checkpointer.adelete_thread was called
        mock_checkpointer.adelete_thread.assert_called_once_with('test-thread')

        # Verify graph.aupdate_state was called with correct arguments
        mock_graph.aupdate_state.assert_called_once()
        call_args = mock_graph.aupdate_state.call_args
        config = call_args[0][0]
        state_update = call_args[0][1]

        # Verify config
        self.assertEqual(config['configurable']['thread_id'], 'test-thread')
        self.assertEqual(config['configurable']['checkpoint_ns'], '')

        # Verify state_update has new messages
        messages = state_update['messages']
        self.assertEqual(len(messages), 4)  # summary (Human) + ack (AI) + 2 preserved

        # First message should be the summary (HumanMessage)
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual(messages[0].content, "[Previous conversation summary]\nSummary of old messages")
        self.assertEqual(messages[0].additional_kwargs, {'summary': True})

        # Second message should be the AI acknowledgment
        self.assertIsInstance(messages[1], AIMessage)
        self.assertEqual(
            messages[1].content,
            "I understand the previous conversation summary. How can I help you continue?"
        )
        self.assertEqual(messages[1].additional_kwargs, {'summary_ack': True})

        # Remaining messages should be preserved
        self.assertEqual(messages[2], preserved_messages[0])
        self.assertEqual(messages[3], preserved_messages[1])


class SummarizerAgentTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.agent = MagicMock()
        self.summarizer = SummarizerAgent(agent=self.agent)

    def test_summarize_conversation_fallback(self):
        """Test summarization fallback when no LLM available."""
        self.summarizer.llm = None

        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
            HumanMessage(content="How are you?"),
            AIMessage(content="I'm doing well")
        ]

        result = asyncio.run(self.summarizer._summarize_conversation(messages, 100))
        self.assertIn("2 user messages", result)
        self.assertIn("2 AI responses", result)

    def test_create_llm_prefers_agent_llm_and_custom_factory(self):
        direct_llm = object()
        self.summarizer.agent_llm = direct_llm
        self.assertIs(self.summarizer._create_llm(), direct_llm)

        class FakeProvider:
            def __init__(
                self,
                name,
                provider_type,
                model,
                api_key,
                base_url,
                additional_config,
                max_context_tokens,
                user,
            ):
                self.name = name
                self.provider_type = provider_type
                self.model = model
                self.api_key = api_key
                self.base_url = base_url
                self.additional_config = additional_config
                self.max_context_tokens = max_context_tokens
                self.user = user

        provider = FakeProvider(
            name="Provider",
            provider_type="openai",
            model="gpt-4o",
            api_key="key",
            base_url="https://example.com",
            additional_config={},
            max_context_tokens=4096,
            user=self.user,
        )
        custom_summarizer = SummarizerAgent(model_name="summary-model", agent=SimpleNamespace(_llm_provider=provider))

        with patch.object(
            llm_agent_mod,
            "_provider_factories",
            {"openai": lambda provider_copy: provider_copy},
            create=True,
        ):
            llm = custom_summarizer._create_llm()

        self.assertEqual(llm.model, "summary-model")

    def test_create_llm_returns_none_without_factory_or_provider(self):
        summarizer = SummarizerAgent(model_name="summary-model", agent=SimpleNamespace(_llm_provider=None))
        self.assertIsNone(summarizer._create_llm())

    async def test_summarize_conversation_with_llm(self):
        """Test summarization using LLM."""
        # Setup mock LLM
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "[THINK]internal[/THINK] Test summary"
        mock_llm.ainvoke.return_value = mock_response
        self.summarizer.llm = mock_llm
        self.summarizer.agent = SimpleNamespace(silent_config={"callbacks": ["langfuse"]})

        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there")
        ]

        result = await self.summarizer._summarize_conversation(messages, 100)

        # Verify LLM was called
        mock_llm.ainvoke.assert_awaited_once()
        _, invoke_kwargs = mock_llm.ainvoke.await_args
        self.assertEqual(invoke_kwargs["config"], {"callbacks": ["langfuse"]})
        self.assertEqual(result, "Test summary")

    async def test_summarize_conversation_llm_failure(self):
        """Test summarization when LLM fails."""
        # Setup mock LLM to fail
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM error")
        self.summarizer.llm = mock_llm

        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there")
        ]

        with self.assertLogs("nova.llm.summarization_middleware", level="WARNING") as logs:
            result = await self.summarizer._summarize_conversation(messages, 100)

        # Should return fallback summary
        self.assertIn("LLM failed", result)
        self.assertTrue(any("LLM summarization failed: LLM error" in line for line in logs.output))

    async def test_summarize_conversation_dispatches_strategies(self):
        with patch.object(self.summarizer, "_summarize_by_topic", AsyncMock(return_value="topic")), patch.object(
            self.summarizer,
            "_summarize_temporal",
            AsyncMock(return_value="temporal"),
        ), patch.object(
            self.summarizer,
            "_summarize_hybrid",
            AsyncMock(return_value="hybrid"),
        ), patch.object(
            self.summarizer,
            "_summarize_conversation",
            AsyncMock(return_value="conversation"),
        ):
            self.assertEqual(
                await self.summarizer.summarize_conversation([], "topic", 50, 2),
                "topic",
            )
            self.assertEqual(
                await self.summarizer.summarize_conversation([], "temporal", 50, 2),
                "temporal",
            )
            self.assertEqual(
                await self.summarizer.summarize_conversation([], "hybrid", 50, 2),
                "hybrid",
            )
            self.assertEqual(
                await self.summarizer.summarize_conversation([], "unknown", 50, 2),
                "conversation",
            )


class SummarizationMiddlewareAsyncTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent_config = MagicMock()
        self.agent_config.auto_summarize = True
        self.agent_config.token_threshold = 100
        self.agent_config.preserve_recent = 2
        self.agent_config.strategy = "conversation"
        self.agent_config.max_summary_length = 300
        self.agent_config.summary_model = None
        self.agent = MagicMock()
        self.agent.config = {"configurable": {"thread_id": "thread-1"}}
        self.agent.agent_config.llm_provider.max_context_tokens = 120
        self.middleware = SummarizationMiddleware(self.agent_config, self.agent)

    async def test_token_counter_returns_zero_without_checkpoint_and_closes_connection(self):
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = None
        fake_checkpointer.conn.close = AsyncMock()

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            count = await TokenCounter.count_context_tokens(self.agent)

        self.assertEqual(count, 0)
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_token_counter_counts_messages_when_checkpoint_exists(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {
            "channel_values": {
                "messages": [HumanMessage(content="hello"), AIMessage(content="hi")],
            }
        }
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()
        self.agent.count_tokens = AsyncMock(return_value=23)

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            count = await TokenCounter.count_context_tokens(self.agent)

        self.assertEqual(count, 23)
        self.agent.count_tokens.assert_awaited_once()

    async def test_manual_summarize_no_history_closes_connection(self):
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = None
        fake_checkpointer.conn.close = AsyncMock()

        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())
        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            result = await self.middleware.manual_summarize(context)

        self.assertEqual(result["status"], "error")
        self.assertIn("No conversation history", result["message"])
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_manual_summarize_not_enough_messages_closes_connection(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {"channel_values": {"messages": [HumanMessage(content="only one")]}}

        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()

        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())
        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            result = await self.middleware.manual_summarize(context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Not enough messages to summarize", result["message"])
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_manual_summarize_success_calls_perform_and_closes_connection(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="m1"),
                    AIMessage(content="m2"),
                    HumanMessage(content="m3"),
                ]
            }
        }

        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()

        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())
        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            with patch.object(self.middleware, "_perform_summarization", new_callable=AsyncMock) as mocked_perform:
                result = await self.middleware.manual_summarize(context)

        self.assertEqual(result["status"], "success")
        mocked_perform.assert_awaited_once_with(context)
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_manual_summarize_returns_error_when_perform_raises(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="m1"),
                    AIMessage(content="m2"),
                    HumanMessage(content="m3"),
                ]
            }
        }
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()
        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer), patch.object(
            self.middleware,
            "_perform_summarization",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await self.middleware.manual_summarize(context)

        self.assertEqual(result["status"], "error")
        self.assertIn("Summarization failed: boom", result["message"])
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_should_summarize_uses_max_context_cap(self):
        # max_context_tokens=120 => threshold capped at 96 (80%)
        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())
        with patch.object(TokenCounter, "count_context_tokens", return_value=97):
            should = await self.middleware._should_summarize(context)
        self.assertTrue(should)

    async def test_perform_summarization_no_checkpoint_still_closes_connection(self):
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = None
        fake_checkpointer.conn.close = AsyncMock()
        context = AgentContext(
            agent_config=MagicMock(name="agent"),
            user=MagicMock(),
            thread=MagicMock(),
            progress_handler=AsyncMock(),
        )

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer):
            await self.middleware._perform_summarization(context)

        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_after_message_only_summarizes_when_threshold_is_reached(self):
        context = AgentContext(agent_config=self.agent_config, user=MagicMock(), thread=MagicMock())

        with patch.object(self.middleware, "_should_summarize", AsyncMock(return_value=False)), patch.object(
            self.middleware,
            "_perform_summarization",
            AsyncMock(),
        ) as mocked_perform:
            await self.middleware.after_message(context, {"messages": []})

        mocked_perform.assert_not_awaited()

        with patch.object(self.middleware, "_should_summarize", AsyncMock(return_value=True)), patch.object(
            self.middleware,
            "_perform_summarization",
            AsyncMock(),
        ) as mocked_perform:
            await self.middleware.after_message(context, {"messages": []})

        mocked_perform.assert_awaited_once_with(context)

    async def test_perform_summarization_success_notifies_progress_and_completion(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="m1"),
                    AIMessage(content="m2"),
                    HumanMessage(content="m3"),
                    AIMessage(content="m4"),
                ]
            }
        }
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()
        progress_handler = AsyncMock()
        context = AgentContext(
            agent_config=SimpleNamespace(name="Summarizer Agent"),
            user=MagicMock(),
            thread=MagicMock(),
            progress_handler=progress_handler,
        )
        self.agent.count_tokens = AsyncMock(return_value=120)

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer), patch.object(
            self.middleware.summarizer,
            "summarize_conversation",
            AsyncMock(return_value="short summary"),
        ), patch.object(
            self.middleware,
            "_inject_summary_into_checkpoint",
            AsyncMock(),
        ) as mocked_inject:
            await self.middleware._perform_summarization(context)

        progress_handler.on_progress.assert_awaited_once()
        progress_handler.on_summarization_complete.assert_awaited_once()
        mocked_inject.assert_awaited_once()
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_perform_summarization_reports_failure_to_progress_handler(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="m1"),
                    AIMessage(content="m2"),
                    HumanMessage(content="m3"),
                ]
            }
        }
        fake_checkpointer = AsyncMock()
        fake_checkpointer.aget_tuple.return_value = fake_checkpoint
        fake_checkpointer.conn.close = AsyncMock()
        progress_handler = AsyncMock()
        context = AgentContext(
            agent_config=SimpleNamespace(name="Broken Summarizer"),
            user=MagicMock(),
            thread=MagicMock(),
            progress_handler=progress_handler,
        )
        self.agent.count_tokens = AsyncMock(return_value=90)

        with patch("nova.llm.summarization_middleware.get_checkpointer", return_value=fake_checkpointer), patch.object(
            self.middleware.summarizer,
            "summarize_conversation",
            AsyncMock(side_effect=RuntimeError("summary failed")),
        ):
            await self.middleware._perform_summarization(context)

        progress_handler.on_progress.assert_any_await(
            "Summarization failed, continuing with full context"
        )
        fake_checkpointer.conn.close.assert_awaited_once()

    async def test_inject_summary_into_checkpoint_reraises_failures(self):
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {"channel_values": {"messages": []}}
        fake_checkpoint.config = {"configurable": {"thread_id": "thread-1"}}
        fake_checkpointer = AsyncMock()
        self.agent.langchain_agent = AsyncMock()
        self.agent.langchain_agent.aupdate_state.side_effect = RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await self.middleware._inject_summary_into_checkpoint(
                "summary",
                [],
                fake_checkpoint,
                fake_checkpointer,
            )
