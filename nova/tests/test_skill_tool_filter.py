import json
from unittest import TestCase

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from nova.llm.skill_policy import SkillLoadingPolicy, apply_skill_policy_to_tool
from nova.llm.skill_tool_filter import filter_tools_for_skills, resolve_active_skills


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


class SkillToolFilterTests(TestCase):
    def test_resolve_active_skills_scopes_to_current_turn(self):
        old_turn_activation = ToolMessage(
            name="load_skill",
            tool_call_id="call-1",
            content=json.dumps({"status": "loaded", "skill": "mail"}),
        )
        new_turn_activation = ToolMessage(
            name="load_skill",
            tool_call_id="call-2",
            content=json.dumps({"status": "loaded", "skill": "mail"}),
        )

        messages = [
            HumanMessage(content="old question"),
            old_turn_activation,
            HumanMessage(content="new question"),
            new_turn_activation,
        ]
        active = resolve_active_skills(messages, {"mail"})

        self.assertEqual(active, {"mail"})

    def test_filter_tools_for_skills_hides_inactive_skill_tools(self):
        regular_tool = _make_tool("date_time")
        mail_tool = _make_tool("list_emails")
        control_tool = _make_tool("load_skill")
        setattr(control_tool, "_nova_skill_control", True)
        apply_skill_policy_to_tool(
            mail_tool,
            SkillLoadingPolicy(mode="skill", skill_id="mail", skill_label="Mail"),
        )

        filtered_no_skill = filter_tools_for_skills(
            [regular_tool, control_tool, mail_tool],
            active_skill_ids=set(),
        )
        self.assertEqual(
            [tool.name for tool in filtered_no_skill],
            ["date_time", "load_skill"],
        )

        filtered_mail = filter_tools_for_skills(
            [regular_tool, control_tool, mail_tool],
            active_skill_ids={"mail"},
        )
        self.assertEqual(
            [tool.name for tool in filtered_mail],
            ["date_time", "load_skill", "list_emails"],
        )
