# nova/tests/test_summarization_middleware.py
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from nova.llm.summarization_middleware import SummarizationMiddleware, SummarizerAgent, TokenCounter
from nova.llm.agent_middleware import AgentContext
from nova.models.SummarizationConfig import SummarizationConfig
from nova.tests.base import BaseTestCase


class SummarizationMiddlewareTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.config = SummarizationConfig(
            auto_summarize=True,
            token_threshold=100,
            preserve_recent=2,
            strategy='conversation',
            max_summary_length=500
        )
        self.agent = MagicMock()
        self.middleware = SummarizationMiddleware(self.config, self.agent)

    def test_should_summarize_disabled(self):
        """Test that summarization is not triggered when disabled."""
        self.config.auto_summarize = False
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

        # Call the method
        await self.middleware._inject_summary_into_checkpoint(
            summary, preserved_messages, mock_checkpoint, mock_checkpointer
        )

        # Verify checkpointer.aput was called
        mock_checkpointer.aput.assert_called_once()

        # Get the call arguments
        call_args = mock_checkpointer.aput.call_args
        config_arg = call_args[1]['config']
        checkpoint_arg = call_args[1]['checkpoint']
        metadata_arg = call_args[1]['metadata']

        # Verify config
        self.assertEqual(config_arg['configurable']['thread_id'], 'test-thread')
        self.assertEqual(config_arg['configurable']['checkpoint_ns'], '')

        # Verify checkpoint has new messages
        messages = checkpoint_arg['channel_values']['messages']
        self.assertEqual(len(messages), 3)  # summary + 2 preserved

        # First message should be the summary
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIn("Previous conversation summary", messages[0].content)
        self.assertIn(summary, messages[0].content)

        # Remaining messages should be preserved
        self.assertEqual(messages[1], preserved_messages[0])
        self.assertEqual(messages[2], preserved_messages[1])

        # Verify metadata is preserved
        self.assertEqual(metadata_arg, mock_checkpoint.metadata)


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
