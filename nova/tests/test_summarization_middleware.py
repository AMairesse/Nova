# nova/tests/test_summarization_middleware.py
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage

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

        # Verify graph.ainvoke was called with correct arguments
        mock_graph.ainvoke.assert_called_once()
        call_args = mock_graph.ainvoke.call_args
        dummy_input = call_args[0][0]
        config = call_args[1]['config']

        # Verify config
        self.assertEqual(config['configurable']['thread_id'], 'test-thread')
        self.assertEqual(config['configurable']['checkpoint_ns'], '')

        # Verify dummy input has new messages
        messages = dummy_input['messages']
        self.assertEqual(len(messages), 3)  # summary + 2 preserved

        # First message should be the summary
        self.assertIsInstance(messages[0], AIMessage)
        self.assertEqual(messages[0].content, summary)
        self.assertEqual(messages[0].additional_kwargs, {'summary': True})

        # Remaining messages should be preserved
        self.assertEqual(messages[1], preserved_messages[0])
        self.assertEqual(messages[2], preserved_messages[1])


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

    async def test_summarize_conversation_with_llm(self):
        """Test summarization using LLM."""
        # Setup mock LLM
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "Test summary"
        mock_llm.ainvoke.return_value = mock_response
        self.summarizer.llm = mock_llm

        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there")
        ]

        result = await self.summarizer._summarize_conversation(messages, 100)

        # Verify LLM was called
        mock_llm.ainvoke.assert_called_once()
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

        result = await self.summarizer._summarize_conversation(messages, 100)

        # Should return fallback summary
        self.assertIn("LLM failed", result)
