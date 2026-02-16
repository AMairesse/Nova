import sys
from types import ModuleType
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from langchain_core.tools import StructuredTool

from nova.llm.llm_tools import load_tools


def _make_tool(name: str) -> StructuredTool:
    async def _noop() -> str:
        return "ok"

    return StructuredTool.from_function(
        coroutine=_noop,
        name=name,
        description="test",
        args_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )


class LLMToolsAggregationTests(IsolatedAsyncioTestCase):
    async def test_uses_aggregated_hook_when_threshold_is_reached(self):
        aggregated_tools = [_make_tool("aggregated_email_tool")]
        fake_module = SimpleNamespace(
            AGGREGATION_SPEC={"min_instances": 2},
            get_functions=AsyncMock(return_value=[]),
            get_aggregated_functions=AsyncMock(return_value=aggregated_tools),
            get_aggregated_prompt_instructions=AsyncMock(return_value=["agg-email-hint"]),
        )
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        agent = SimpleNamespace(
            builtin_tools=[
                SimpleNamespace(id=11, tool_subtype="email", python_path="nova.tools.builtins.email"),
                SimpleNamespace(id=12, tool_subtype="email", python_path="nova.tools.builtins.email"),
            ],
            mcp_tools_data=[],
            has_agent_tools=False,
            agent_tools=[],
            thread=SimpleNamespace(mode="thread"),
            agent_config=SimpleNamespace(is_tool=False),
            _loaded_builtin_modules=[],
        )

        with patch("nova.tools.import_module", return_value=fake_module):
            with patch.dict(sys.modules, {"nova.tools.files": fake_files_module}):
                tools = await load_tools(agent)

        self.assertIn("aggregated_email_tool", [tool.name for tool in tools])
        fake_module.get_aggregated_functions.assert_awaited_once()
        fake_module.get_functions.assert_not_awaited()
        self.assertIn("agg-email-hint", getattr(agent, "tool_prompt_hints", []))

    async def test_falls_back_to_legacy_loader_below_threshold(self):
        legacy_tools = [_make_tool("legacy_email_tool")]
        fake_module = SimpleNamespace(
            AGGREGATION_SPEC={"min_instances": 2},
            get_functions=AsyncMock(return_value=legacy_tools),
            get_aggregated_functions=AsyncMock(return_value=[]),
            get_prompt_instructions=lambda: ["legacy-email-hint"],
        )
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        agent = SimpleNamespace(
            builtin_tools=[
                SimpleNamespace(id=21, tool_subtype="email", python_path="nova.tools.builtins.email"),
            ],
            mcp_tools_data=[],
            has_agent_tools=False,
            agent_tools=[],
            thread=SimpleNamespace(mode="thread"),
            agent_config=SimpleNamespace(is_tool=False),
            _loaded_builtin_modules=[],
        )

        with patch("nova.tools.import_module", return_value=fake_module):
            with patch.dict(sys.modules, {"nova.tools.files": fake_files_module}):
                tools = await load_tools(agent)

        self.assertIn("legacy_email_tool", [tool.name for tool in tools])
        fake_module.get_functions.assert_awaited_once()
        fake_module.get_aggregated_functions.assert_not_awaited()
        self.assertIn("legacy-email-hint", getattr(agent, "tool_prompt_hints", []))

    async def test_non_aggregated_module_keeps_per_tool_loading(self):
        async def _legacy_loader(tool, agent):
            return [_make_tool(f"legacy_tool_{tool.id}")]

        fake_module = SimpleNamespace(
            get_functions=AsyncMock(side_effect=_legacy_loader),
            get_prompt_instructions=lambda: ["regular-hint"],
        )
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        agent = SimpleNamespace(
            builtin_tools=[
                SimpleNamespace(id=31, tool_subtype="alpha", python_path="nova.tools.builtins.alpha"),
                SimpleNamespace(id=32, tool_subtype="alpha", python_path="nova.tools.builtins.alpha"),
            ],
            mcp_tools_data=[],
            has_agent_tools=False,
            agent_tools=[],
            thread=SimpleNamespace(mode="thread"),
            agent_config=SimpleNamespace(is_tool=False),
            _loaded_builtin_modules=[],
        )

        with patch("nova.tools.import_module", return_value=fake_module):
            with patch.dict(sys.modules, {"nova.tools.files": fake_files_module}):
                tools = await load_tools(agent)

        names = {tool.name for tool in tools}
        self.assertIn("legacy_tool_31", names)
        self.assertIn("legacy_tool_32", names)
        self.assertEqual(fake_module.get_functions.await_count, 2)
        self.assertIn("regular-hint", getattr(agent, "tool_prompt_hints", []))
