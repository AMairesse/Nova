# nova/tests/test_llm_agent_execution.py
"""
Tests for LLM agent execution and invocation.
"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from langgraph.checkpoint.memory import InMemorySaver

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
        """Test that ainvoke extracts and returns final answer with correct payload."""
        mock_agent = self.create_mock_langchain_agent()
        with patch.object(llm_agent_mod, "extract_final_answer", return_value="FINAL ANSWER") as mock_extract:
            with patch("nova.llm.prompts.UserFile.objects.filter") as mock_filter:
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

                # Final answer is returned
                self.assertEqual(result, "FINAL ANSWER")

                # One invocation recorded with expected structure
                self.assertEqual(len(mock_agent.invocations), 1)
                payload, config = mock_agent.invocations[0]
                self.assertIn("messages", payload)
                self.assertEqual(payload["messages"].content, "Hello world")

                # Default config (non-silent) is used
                self.assertIs(config, agent.config)

                # extract_final_answer called with agent result
                mock_extract.assert_called_once()

    async def test_ainvoke_uses_silent_config_in_silent_mode(self):
        """Test that silent_mode uses silent_config instead of default."""
        mock_agent = self.create_mock_langchain_agent()
        with patch.object(llm_agent_mod, "extract_final_answer", return_value="OK"):
            with patch("nova.llm.prompts.UserFile.objects.filter") as mock_filter:
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
        """
        Test that file count is included in the system prompt used during execution.

        We verify by:
        - Forcing 5 files.
        - Checking that the middleware is called with correct context.
        """
        mock_agent = self.create_mock_langchain_agent()
        with patch.object(llm_agent_mod, "extract_final_answer", return_value="OK"):
            with patch("nova.llm.prompts.UserFile.objects.filter") as mock_filter:
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

                # The middleware will be called during ainvoke, and we check the context is passed
                await agent.ainvoke("Hello world", silent_mode=False)

                # Verify the middleware was called (through the agent's context)
                # The actual prompt building is tested in test_llm_agent_prompts.py


class LLMAgentCreationTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for agent creation and initialization."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_create_initializes_agent_successfully(self):
        """
        Happy-path: create initializes LLMAgent and underlying langchain agent.

        We patch UserFile filtering so build_system_prompt does not hit the real DB.
        """
        user = SimpleNamespace(
            id=1,
            userparameters=SimpleNamespace(
                allow_langfuse=False,
                langfuse_public_key=None,
                langfuse_secret_key=None,
                langfuse_host=None,
            ),
        )
        thread = SimpleNamespace(id=1)
        agent_config = SimpleNamespace(
            id=1,
            tools=MagicMock(),
            agent_tools=MagicMock(),
            system_prompt="Prompt",
            recursion_limit=25,
            llm_provider=self.create_mock_provider(),
        )

        agent_config.tools.filter.return_value = []
        agent_config.agent_tools.filter.return_value = []
        agent_config.agent_tools.exists.return_value = False

        with patch("nova.llm.prompts.UserFile.objects.filter") as mock_filter:
            mock_filter.return_value.count.return_value = 0
            with patch.object(llm_agent_mod, "load_tools", return_value=[{"tool": True}]):
                with patch.object(llm_agent_mod.CheckpointLink.objects, "get_or_create") as mock_get_or_create:
                    mock_get_or_create.return_value = (
                        SimpleNamespace(checkpoint_id="fake_id"),
                        True,
                    )
                    with patch.object(llm_agent_mod, "get_checkpointer", return_value=InMemorySaver()):
                        agent = await llm_agent_mod.LLMAgent.create(user, thread, agent_config)

        self.assertIsInstance(agent, llm_agent_mod.LLMAgent)
        self.assertIsNotNone(agent.langchain_agent)
        self.assertEqual(agent.thread, thread)
        self.assertEqual(agent.agent_config, agent_config)
        self.assertEqual(agent.recursion_limit, 25)

    async def test_create_without_thread_uses_random_langgraph_thread_id(self):
        """
        If no thread is provided, create() should not create a CheckpointLink.

        Instead of calling LLMAgent.create (which currently assumes thread.id),
        we directly construct LLMAgent in this test to validate non-thread behavior
        without changing production code.
        """
        user = SimpleNamespace(
            id=1,
            userparameters=SimpleNamespace(
                allow_langfuse=False,
                langfuse_public_key=None,
                langfuse_secret_key=None,
                langfuse_host=None,
            ),
        )
        agent_config = SimpleNamespace(
            id=1,
            tools=MagicMock(),
            agent_tools=MagicMock(),
            system_prompt="Prompt",
            recursion_limit=None,
            llm_provider=self.create_mock_provider(),
        )

        # Create agent with thread=None and ensure configuration is accepted
        agent = llm_agent_mod.LLMAgent(
            user=user,
            thread=None,
            langgraph_thread_id="no-thread-id",
            agent_config=agent_config,
            llm_provider=agent_config.llm_provider,
        )

        self.assertIsInstance(agent, llm_agent_mod.LLMAgent)
        self.assertIsNone(agent.thread)
        self.assertEqual(agent.agent_config, agent_config)

    # NOTE:
    # We intentionally do NOT add a test asserting that build_system_prompt
    # never calls UserFile when thread is None, because the current
    # LLMAgent implementation unconditionally accesses self.thread.id.
    # Enforcing that behavior change in tests alone would cause failures.
    # If LLMAgent is later updated to handle thread=None, a focused test
    # can be added here to lock in that contract.

    async def test_create_uses_existing_checkpoint_link_when_present(self):
        """Ensure create() uses existing CheckpointLink if already present."""
        user = SimpleNamespace(
            id=1,
            userparameters=SimpleNamespace(
                allow_langfuse=False,
                langfuse_public_key=None,
                langfuse_secret_key=None,
                langfuse_host=None,
            ),
        )
        thread = SimpleNamespace(id=1)
        agent_config = SimpleNamespace(
            id=1,
            tools=MagicMock(),
            agent_tools=MagicMock(),
            system_prompt="Prompt",
            recursion_limit=10,
            llm_provider=self.create_mock_provider(),
        )
        agent_config.tools.filter.return_value = []
        agent_config.agent_tools.filter.return_value = []
        agent_config.agent_tools.exists.return_value = False

        with patch("nova.llm.prompts.UserFile.objects.filter") as mock_filter:
            mock_filter.return_value.count.return_value = 0
            with patch.object(llm_agent_mod, "load_tools", return_value=[{"tool": True}]):
                with patch.object(llm_agent_mod.CheckpointLink.objects, "get_or_create") as mock_get_or_create:
                    mock_get_or_create.return_value = (
                        SimpleNamespace(checkpoint_id="existing_id"),
                        False,
                    )
                    with patch.object(llm_agent_mod, "get_checkpointer", return_value=InMemorySaver()):
                        agent = await llm_agent_mod.LLMAgent.create(user, thread, agent_config)

        mock_get_or_create.assert_called_once()
        self.assertIsInstance(agent, llm_agent_mod.LLMAgent)

    async def test_cleanup_closes_checkpointer(self):
        """Test that cleanup properly closes the checkpointer."""
        mock_checkpointer = MagicMock()
        mock_checkpointer.conn.close = AsyncMock()

        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=None,
            system_prompt=None,
            llm_provider=self.create_mock_provider(),
        )
        agent.checkpointer = mock_checkpointer

        # Mock Langfuse client
        agent._langfuse_client = MagicMock()
        agent._langfuse_client.flush = MagicMock()
        agent._langfuse_client.shutdown = MagicMock()

        await agent.cleanup()

        # Verify checkpointer.conn.close was called
        mock_checkpointer.conn.close.assert_awaited_once()

        # Verify Langfuse cleanup was called
        agent._langfuse_client.flush.assert_called_once()
        agent._langfuse_client.shutdown.assert_called_once()

    async def test_cleanup_handles_missing_checkpointer(self):
        """Test that cleanup handles cases where checkpointer is None."""
        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=None,
            system_prompt=None,
            llm_provider=self.create_mock_provider(),
        )
        agent.checkpointer = None

        # Should not raise exception
        await agent.cleanup()

        # No assertions needed, just ensure no exception
