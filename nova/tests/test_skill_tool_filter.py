import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from nova.llm.skill_policy import SkillLoadingPolicy, apply_skill_policy_to_tool
from nova.llm.skill_tool_filter import (
    _content_to_text,
    _extract_loaded_skill_from_message,
    apply_skill_tool_filter,
    filter_tools_for_skills,
    resolve_active_skills,
)


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

    def test_content_to_text_and_extract_loaded_skill_handle_multiple_payload_shapes(self):
        self.assertEqual(
            _content_to_text(["hello", {"text": "world"}, {"kind": "meta"}, 5]),
            'hello\nworld\n{"kind": "meta"}\n5',
        )
        self.assertEqual(_content_to_text({"status": "loaded"}), '{"status": "loaded"}')
        self.assertEqual(_content_to_text(None), "")

        loaded = ToolMessage(
            name="load_skill",
            tool_call_id="call-3",
            content=[{"text": json.dumps({"status": "loaded", "skill": "Mail Inbox"})}],
        )
        invalid = ToolMessage(
            name="load_skill",
            tool_call_id="call-4",
            content=json.dumps({"status": "failed", "skill": "mail"}),
        )

        self.assertEqual(_extract_loaded_skill_from_message(loaded), "mail_inbox")
        self.assertIsNone(_extract_loaded_skill_from_message(invalid))

    def test_resolve_active_skills_supports_custom_load_tool_names_and_no_human_turn(self):
        no_human_messages = [
            ToolMessage(
                name="load_skill",
                tool_call_id="call-1",
                content=json.dumps({"status": "loaded", "skill": "mail"}),
            )
        ]
        custom_messages = [
            HumanMessage(content="current turn"),
            ToolMessage(
                name="load_skill__dup",
                tool_call_id="call-2",
                content=json.dumps({"status": "loaded", "skill": "mail"}),
            ),
        ]

        self.assertEqual(resolve_active_skills(no_human_messages, {"mail"}), set())
        self.assertEqual(
            resolve_active_skills(
                custom_messages,
                {"mail"},
                load_skill_tool_names={"load_skill__dup"},
            ),
            {"mail"},
        )

    def test_filter_tools_for_skills_keeps_dict_entries(self):
        filtered = filter_tools_for_skills(
            [{"name": "dict-tool"}],
            active_skill_ids=set(),
        )

        self.assertEqual(filtered, [{"name": "dict-tool"}])

    def test_apply_skill_tool_filter_passthrough_without_catalog(self):
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=SimpleNamespace(skill_catalog={}, active_skill_ids=None)),
            state={},
            tools=["tool-a"],
        )
        handler = AsyncMock(return_value="ok")

        result = async_to_sync(apply_skill_tool_filter.awrap_model_call)(request, handler)

        self.assertEqual(result, "ok")
        self.assertEqual(request.runtime.context.active_skill_ids, [])
        handler.assert_awaited_once_with(request)

    def test_apply_skill_tool_filter_filters_tools_for_active_skills(self):
        regular_tool = _make_tool("date_time")
        mail_tool = _make_tool("list_emails")
        control_tool = _make_tool("load_skill")
        setattr(control_tool, "_nova_skill_control", True)
        apply_skill_policy_to_tool(
            mail_tool,
            SkillLoadingPolicy(mode="skill", skill_id="mail", skill_label="Mail"),
        )

        class FakeRequest(SimpleNamespace):
            def override(self, **kwargs):
                data = self.__dict__.copy()
                data.update(kwargs)
                return FakeRequest(**data)

        request = FakeRequest(
            runtime=SimpleNamespace(
                context=SimpleNamespace(
                    skill_catalog={"mail": {"label": "Mail"}},
                    skill_control_tool_names=["load_skill"],
                    active_skill_ids=None,
                )
            ),
            state={
                "messages": [
                    HumanMessage(content="Help with mail"),
                    ToolMessage(
                        name="load_skill",
                        tool_call_id="call-5",
                        content=json.dumps({"status": "loaded", "skill": "mail"}),
                    ),
                ]
            },
            tools=[regular_tool, control_tool, mail_tool],
        )
        handler = AsyncMock(side_effect=lambda req: [tool.name for tool in req.tools])

        result = async_to_sync(apply_skill_tool_filter.awrap_model_call)(request, handler)

        self.assertEqual(result, ["date_time", "load_skill", "list_emails"])
        self.assertEqual(request.runtime.context.active_skill_ids, ["mail"])
