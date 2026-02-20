import sys
from types import ModuleType, SimpleNamespace
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


class LLMToolsSkillsTests(IsolatedAsyncioTestCase):
    async def test_load_tools_registers_skill_catalog_and_control_tools(self):
        fake_module = SimpleNamespace(
            METADATA={
                "name": "Email (IMAP/SMTP)",
                "loading": {
                    "mode": "skill",
                    "skill_id": "mail",
                    "skill_label": "Mail",
                },
            },
            get_functions=AsyncMock(return_value=[_make_tool("list_emails")]),
            get_prompt_instructions=lambda: [],
            get_skill_instructions=lambda **kwargs: ["Use preview mode first."],
        )
        fake_files_module = ModuleType("nova.tools.files")
        fake_files_module.get_functions = AsyncMock(return_value=[])

        agent = SimpleNamespace(
            builtin_tools=[
                SimpleNamespace(
                    id=41,
                    tool_subtype="email",
                    python_path="nova.tools.builtins.email",
                )
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
        self.assertIn("list_emails", names)
        self.assertIn("list_skills", names)
        self.assertIn("load_skill", names)

        self.assertIn("mail", agent.skill_catalog)
        self.assertEqual(agent.skill_catalog["mail"]["label"], "Mail")
        self.assertIn("list_emails", agent.skill_catalog["mail"]["tool_names"])
        self.assertIn("Use preview mode first.", agent.skill_catalog["mail"]["instructions"])
        self.assertIn("load_skill", agent.skill_control_tool_names)
