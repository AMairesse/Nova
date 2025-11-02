# nova/tests/test_llm_agent_prompts.py
"""
Tests for LLM agent system prompt building functionality.
"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import nova.llm.llm_agent as llm_agent_mod
from .test_llm_agent_mixins import LLMAgentTestMixin


class LLMAgentPromptTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for system prompt building and templating."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_build_system_prompt_default(self):
        """Test default system prompt when no custom prompt provided."""
        with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
            mock_filter.return_value.count.return_value = 0

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=None,
            )

            prompt = await agent.build_system_prompt()
            self.assertIn("You are a helpful assistant", prompt)

    async def test_build_system_prompt_with_template_variables(self):
        """Test system prompt with template variables like {today}."""
        with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
            mock_filter.return_value.count.return_value = 0

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=None,
                system_prompt="Today is {today}.",
            )

            prompt = await agent.build_system_prompt()
            self.assertNotIn("{today}", prompt)
            self.assertTrue(prompt.startswith("Today is "))

    async def test_build_system_prompt_with_file_context(self):
        """Test system prompt includes file attachment information."""
        # Mock UserFile.objects.filter to simulate files
        with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
            mock_filter.return_value.count.return_value = 3

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=None,
                system_prompt="Base prompt.",
            )

            prompt = await agent.build_system_prompt()
            self.assertIn("3 file(s) are attached to this thread", prompt)
            self.assertIn("Use file tools if needed", prompt)

    async def test_build_system_prompt_no_files(self):
        """Test system prompt when no files are attached."""
        with patch.object(llm_agent_mod.UserFile.objects, 'filter') as mock_filter:
            mock_filter.return_value.count.return_value = 0

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=None,
                system_prompt="Base prompt.",
            )

            prompt = await agent.build_system_prompt()
            self.assertNotIn("file(s) are attached", prompt)
