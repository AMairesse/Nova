# nova/tests/test_llm_agent_execution.py
"""
Tests for LLM agent execution and invocation.
"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import nova.llm.llm_agent as llm_agent_mod
from .test_llm_agent_mixins import LLMAgentTestMixin


class LLMAgentExecutionTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for agent execution and invocation."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_ainvoke_returns_final_answer(self):
        """Test that ainvoke extracts and returns final answer."""
        mock_agent = self.create_mock_langchain_agent()
        mock_agent.invocations = []

        with patch.object(llm_agent_mod, "extract_final_answer", return_value="FINAL ANSWER"):
            with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
                mock_filter.return_value.count.return_value = 0

                agent = llm_agent_mod.LLMAgent(
                    user=self.create_mock_user(),
                    thread=self.create_mock_thread(),
                    langgraph_thread_id="fake_id",
                    agent_config=None,
                    system_prompt=None,
                    llm_provider=self.create_mock_provider(),
                )
                agent.langchain_agent = mock_agent

                result = await agent.ainvoke("Hello world", silent_mode=False)

                self.assertEqual(result, "FINAL ANSWER")
                self.assertEqual(len(mock_agent.invocations), 1)

    async def test_ainvoke_uses_silent_config_in_silent_mode(self):
        """Test that silent_mode uses silent_config instead of default."""
        mock_agent = self.create_mock_langchain_agent()

        with patch.object(llm_agent_mod, "extract_final_answer", return_value="OK"):
            with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
                mock_filter.return_value.count.return_value = 0

                agent = llm_agent_mod.LLMAgent(
                    user=self.create_mock_user(),
                    thread=self.create_mock_thread(),
                    langgraph_thread_id="fake_id",
                    agent_config=None,
                    system_prompt=None,
                    llm_provider=self.create_mock_provider(),
                )
                agent.langchain_agent = mock_agent

                await agent.ainvoke("Test", silent_mode=True)

                # Verify silent_config was used
                payload, config = mock_agent.invocations[0]
                self.assertIs(config, agent.silent_config)

    async def test_ainvoke_includes_file_context_in_prompt(self):
        """Test that file count is included in system prompt during execution."""
        mock_agent = self.create_mock_langchain_agent()

        with patch.object(llm_agent_mod, "extract_final_answer", return_value="OK"):
            with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
                mock_filter.return_value.count.return_value = 5

                agent = llm_agent_mod.LLMAgent(
                    user=self.create_mock_user(),
                    thread=self.create_mock_thread(),
                    langgraph_thread_id="fake_id",
                    agent_config=None,
                    system_prompt="Base prompt.",
                    llm_provider=self.create_mock_provider(),
                )
                agent.langchain_agent = mock_agent

                await agent.ainvoke("Test", silent_mode=False)

                # Verify system prompt was built with file context
                system_prompt = await agent.build_system_prompt()
                self.assertIn("5 file(s) are attached", system_prompt)


class LLMAgentCreationTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for agent creation and initialization."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_create_initializes_agent_successfully(self):
        """Test successful agent creation with all dependencies."""
        with patch.object(llm_agent_mod.LLMAgent, "fetch_user_params_sync",
                          return_value=(False, None, None, None)):
            with patch.object(llm_agent_mod.LLMAgent, "fetch_agent_data_sync",
                              return_value=([], [], [], False, "prompt", 25,
                                            self.create_mock_provider())):
                with patch("nova.llm.llm_agent.load_tools", return_value=[{"tool": True}]):
                    checkpointer = await self.fakes["nova.llm.checkpoints"].get_checkpointer()
                    with patch("nova.llm.llm_agent.get_checkpointer", return_value=checkpointer):
                        with patch("nova.llm.llm_agent.create_agent",
                                   return_value=self.create_mock_langchain_agent()):
                            with patch.object(llm_agent_mod.CheckpointLink.objects, "get_or_create",
                                              return_value=(SimpleNamespace(checkpoint_id="fake_id"), True)):
                                with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
                                    mock_filter.return_value.count.return_value = 0

                                    user = SimpleNamespace(id=1,
                                                           userparameters=SimpleNamespace(allow_langfuse=False))
                                    thread = SimpleNamespace(id=1)

                                    agent = await llm_agent_mod.LLMAgent.create(user, thread,
                                                                                SimpleNamespace())

                                    self.assertIsNotNone(agent)
                                    self.assertIsInstance(agent, llm_agent_mod.LLMAgent)
                                    self.assertIsNotNone(agent.langchain_agent)
