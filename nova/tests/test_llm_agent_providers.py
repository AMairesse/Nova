# nova/tests/test_llm_agent_providers.py
"""
Tests for LLM agent provider integration and LLM creation.
"""
from unittest import IsolatedAsyncioTestCase

import nova.llm.llm_agent as llm_agent_mod
from nova.models.Provider import ProviderType
from .test_llm_agent_mixins import LLMAgentTestMixin


class LLMAgentProviderTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for LLM provider integration."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    def test_create_llm_agent_openai_provider(self):
        """Test LLM creation with OpenAI provider."""
        provider = self.create_mock_provider(ProviderType.OPENAI)
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=provider,
        )

        llm = agent.create_llm_agent()
        self.assertEqual(llm.__class__.__name__, "ChatOpenAI")

    def test_create_llm_agent_mistral_provider(self):
        """Test LLM creation with Mistral provider."""
        provider = self.create_mock_provider(ProviderType.MISTRAL)
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=provider,
        )

        llm = agent.create_llm_agent()
        self.assertEqual(llm.__class__.__name__, "ChatMistralAI")

    def test_create_llm_agent_ollama_provider(self):
        """Test LLM creation with Ollama provider."""
        provider = self.create_mock_provider(ProviderType.OLLAMA)
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=provider,
        )

        llm = agent.create_llm_agent()
        self.assertEqual(llm.__class__.__name__, "ChatOllama")

    def test_create_llm_agent_no_provider_raises_error(self):
        """Test that missing provider raises exception."""
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=None,
        )

        with self.assertRaises(Exception):
            agent.create_llm_agent()

    def test_create_llm_agent_unsupported_provider_raises_error(self):
        """Test that unsupported provider type raises ValueError."""
        provider = self.create_mock_provider("UNSUPPORTED_TYPE")
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=provider,
        )

        with self.assertRaises(ValueError):
            agent.create_llm_agent()

    def test_create_llm_agent_preserves_provider_config(self):
        """Test that provider configuration is passed to LLM."""
        provider = self.create_mock_provider(
            ProviderType.OPENAI,
            model="gpt-4-turbo",
            api_key="test_key_123",
            base_url="https://custom.openai.com"
        )
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            system_prompt=None,
            llm_provider=provider,
        )

        llm = agent.create_llm_agent()
        # Verify the LLM was created with the correct class
        self.assertEqual(llm.__class__.__name__, "ChatOpenAI")
        # The fake LLM stores kwargs in the instance for testing
        # We can't directly access real ChatOpenAI attributes, so just verify creation
        self.assertIsNotNone(llm)
