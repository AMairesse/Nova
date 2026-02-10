import sys
from types import ModuleType
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from langchain_core.tools import StructuredTool

from nova.llm.llm_tools import load_tools


class LLMToolsContinuousPolicyTests(IsolatedAsyncioTestCase):
    async def test_auto_loads_conversation_tools_for_continuous_main_agent(self):
        agent = SimpleNamespace(
            builtin_tools=[],
            mcp_tools_data=[],
            has_agent_tools=False,
            agent_tools=[],
            thread=SimpleNamespace(mode="continuous"),
            agent_config=SimpleNamespace(is_tool=False),
            _loaded_builtin_modules=[],
        )

        async def _conversation_get_functions(tool, agent):
            return [
                StructuredTool.from_function(
                    coroutine=lambda query: {"ok": True, "query": query},
                    name="conversation_search",
                    description="test",
                    args_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                ),
                StructuredTool.from_function(
                    coroutine=lambda message_id: {"ok": True, "message_id": message_id},
                    name="conversation_get",
                    description="test",
                    args_schema={
                        "type": "object",
                        "properties": {"message_id": {"type": "integer"}},
                        "required": ["message_id"],
                    },
                ),
            ]

        conv_module = SimpleNamespace(get_functions=_conversation_get_functions)
        conv_module.get_prompt_instructions = lambda: ["use conversation_search first"]
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        with patch("nova.continuous.tools.conversation_tools", conv_module, create=True):
            with patch.dict(sys.modules, {"nova.tools.files": fake_files_module}):
                tools = await load_tools(agent)

        names = {t.name for t in tools}
        self.assertIn("conversation_search", names)
        self.assertIn("conversation_get", names)
        self.assertIn("use conversation_search first", getattr(agent, "tool_prompt_hints", []))

    async def test_does_not_auto_load_conversation_tools_for_subagent(self):
        agent = SimpleNamespace(
            builtin_tools=[],
            mcp_tools_data=[],
            has_agent_tools=False,
            agent_tools=[],
            thread=SimpleNamespace(mode="continuous"),
            agent_config=SimpleNamespace(is_tool=True),
            _loaded_builtin_modules=[],
        )
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        with patch.dict(sys.modules, {"nova.tools.files": fake_files_module}):
            tools = await load_tools(agent)

        names = {t.name for t in tools}
        self.assertNotIn("conversation_search", names)
        self.assertNotIn("conversation_get", names)
        self.assertEqual(getattr(agent, "tool_prompt_hints", []), [])
