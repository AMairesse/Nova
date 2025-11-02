# nova/tests/test_llm_agent_tools.py
"""
Tests for LLM agent tool loading and integration.
"""
import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import nova.llm.llm_agent as llm_agent_mod
from .test_llm_agent_mixins import LLMAgentTestMixin


class LLMAgentToolsTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    """Test cases for tool loading and integration."""

    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_load_tools_builtin_tools(self):
        """Test loading of built-in tools."""
        # Setup fake builtin module
        class FakeBuiltinModule:
            def __init__(self):
                self.init_called = False

            async def init(self, agent):
                self.init_called = True

            async def get_functions(self, tool, agent):
                return [{"builtin_tool": tool.tool_subtype}]

            async def close(self, agent):
                pass

        fake_builtin_mod = FakeBuiltinModule()

        # Mock nova.tools
        fake_nova_tools = types.ModuleType("nova.tools")
        fake_nova_tools.import_module = lambda path: fake_builtin_mod

        # Mock nova.tools.files to avoid import error
        fake_files_mod = types.ModuleType("nova.tools.files")

        async def async_get_functions(agent):
            return []

        fake_files_mod.get_functions = async_get_functions

        with patch.dict(sys.modules, {
            "nova.tools": fake_nova_tools,
            "nova.tools.files": fake_files_mod
        }):
            builtin_tools = [SimpleNamespace(
                python_path="nova.tools.builtins.date",
                tool_subtype="date",
                is_active=True,
                tool_type="BUILTIN"
            )]

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=self.create_mock_agent_config(),
                builtin_tools=builtin_tools,
                mcp_tools_data=[],
                agent_tools=[],
                has_agent_tools=False,
                system_prompt=None,
                llm_provider=self.create_mock_provider(),
            )

            tools = await llm_agent_mod.load_tools(agent)

            # Verify builtin tool was loaded and module tracked
            self.assertTrue(any(isinstance(mod, FakeBuiltinModule) for mod in agent._loaded_builtin_modules))
            self.assertIn({"builtin_tool": "date"}, tools)

    async def test_load_tools_mcp_tools(self):
        """Test loading of MCP tools with name sanitization."""
        # Setup fake MCP client
        class FakeMCPClient:
            def __init__(self, endpoint, thread_id=None, credential=None, transport_type=None, user_id=None):
                self.endpoint = endpoint

            def list_tools(self, force_refresh=False):
                return [
                    {"name": "do thing", "description": "desc", "input_schema": {}},
                    {"name": "calc:add", "description": "", "input_schema": {"type": "object"}},
                ]

            async def alist_tools(self, force_refresh=False):
                return self.list_tools(force_refresh=force_refresh)

            async def acall(self, tool_name, **kwargs):
                return {"ok": True}

        # Mock modules
        fake_mcp_mod = types.ModuleType("nova.mcp.client")
        fake_mcp_mod.MCPClient = FakeMCPClient

        fake_lc_tools = types.ModuleType("langchain_core.tools")

        class StructuredTool:
            @classmethod
            def from_function(cls, func=None, coroutine=None, name=None, description=None,
                              args_schema=None, return_direct=None, response_format=None, **kwargs):
                return {
                    "name": name,
                    "description": description,
                    "args_schema": args_schema,
                    "func": func,
                    "coroutine": coroutine
                }

        fake_lc_tools.StructuredTool = StructuredTool

        # Mock nova.tools.files to avoid import error
        fake_files_mod = types.ModuleType("nova.tools.files")

        async def async_get_functions(agent):
            return []

        fake_files_mod.get_functions = async_get_functions

        with patch.dict(sys.modules, {
            "nova.mcp.client": fake_mcp_mod,
            "langchain_core.tools": fake_lc_tools,
            "nova.tools.files": fake_files_mod
        }):
            mcp_tools_data = [(SimpleNamespace(endpoint="https://mcp.example.com", transport_type="http"),
                               SimpleNamespace(user=SimpleNamespace(id=1)), None, 1)]

            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=self.create_mock_agent_config(),
                builtin_tools=[],
                mcp_tools_data=mcp_tools_data,
                agent_tools=[],
                has_agent_tools=False,
                system_prompt=None,
                llm_provider=self.create_mock_provider(),
            )

            tools = await llm_agent_mod.load_tools(agent)

            # Verify MCP tools are loaded with sanitized names
            tool_names = {t.name for t in tools if hasattr(t, 'name')}
            self.assertIn("do_thing", tool_names)
            self.assertIn("calc_add", tool_names)

    async def test_load_tools_agent_tools(self):
        """Test loading of agent-as-tools."""
        agent_tools = [SimpleNamespace(name="delegate_agent")]

        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=self.create_mock_agent_config(),
            builtin_tools=[],
            mcp_tools_data=[],
            agent_tools=agent_tools,
            has_agent_tools=True,
            system_prompt=None,
            llm_provider=self.create_mock_provider(),
        )

        tools = await llm_agent_mod.load_tools(agent)

        # Verify agent tool wrapper was created
        self.assertIn({"wrapped_agent_tool": "delegate_agent"}, tools)

    async def test_load_tools_file_tools(self):
        """Test loading of file tools."""
        fake_files_mod = types.ModuleType("nova.tools.files")

        async def get_functions(agent):
            return [{"file_tool": True}]

        fake_files_mod.get_functions = get_functions

        with patch.dict(sys.modules, {"nova.tools.files": fake_files_mod}):
            agent = llm_agent_mod.LLMAgent(
                user=self.create_mock_user(),
                thread=self.create_mock_thread(),
                langgraph_thread_id="fake_id",
                agent_config=self.create_mock_agent_config(),
                builtin_tools=[],
                mcp_tools_data=[],
                agent_tools=[],
                has_agent_tools=False,
                system_prompt=None,
                llm_provider=self.create_mock_provider(),
            )

            tools = await llm_agent_mod.load_tools(agent)

            # Verify file tools were loaded
            self.assertIn({"file_tool": True}, tools)

    async def test_cleanup_closes_builtin_modules(self):
        """Test that cleanup properly closes loaded builtin modules."""
        class FakeBuiltinModule:
            def __init__(self):
                self.closed = False

            async def close(self, agent):
                self.closed = True

        agent = llm_agent_mod.LLMAgent(
            user=self.create_mock_user(),
            thread=self.create_mock_thread(),
            langgraph_thread_id="fake_id",
            agent_config=None,
        )

        m1, m2 = FakeBuiltinModule(), FakeBuiltinModule()
        agent._loaded_builtin_modules = [m1, m2]

        await agent.cleanup()

        self.assertTrue(m1.closed)
        self.assertTrue(m2.closed)
