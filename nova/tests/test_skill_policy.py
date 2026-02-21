from types import SimpleNamespace
from unittest import TestCase

from langchain_core.tools import StructuredTool

from nova.llm.skill_policy import (
    TOOL_SKILL_ID_ATTR,
    TOOL_SKILL_MODE_ATTR,
    SkillLoadingPolicy,
    apply_skill_policy_to_tool,
    get_module_skill_policy,
    get_tool_skill_id,
    is_skill_tool,
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


class SkillPolicyTests(TestCase):
    def test_get_module_skill_policy_defaults_to_always(self):
        module = SimpleNamespace(METADATA={"name": "Any Tool"})
        policy = get_module_skill_policy(module)

        self.assertEqual(policy.mode, "always")
        self.assertFalse(policy.is_skill)

    def test_get_module_skill_policy_reads_valid_skill_metadata(self):
        module = SimpleNamespace(
            METADATA={
                "name": "Email",
                "loading": {
                    "mode": "skill",
                    "skill_id": "mail",
                    "skill_label": "Mail",
                },
            }
        )
        policy = get_module_skill_policy(module)

        self.assertEqual(policy.mode, "skill")
        self.assertTrue(policy.is_skill)
        self.assertEqual(policy.skill_id, "mail")
        self.assertEqual(policy.skill_label, "Mail")

    def test_get_module_skill_policy_rejects_skill_without_id(self):
        module = SimpleNamespace(
            METADATA={
                "name": "Email",
                "loading": {
                    "mode": "skill",
                    "skill_id": "",
                },
            }
        )
        policy = get_module_skill_policy(module)

        self.assertEqual(policy.mode, "always")
        self.assertIsNone(policy.skill_id)
        self.assertFalse(policy.is_skill)

    def test_apply_skill_policy_marks_tool(self):
        tool = _make_tool("list_emails")
        policy = SkillLoadingPolicy(mode="skill", skill_id="mail", skill_label="Mail")

        apply_skill_policy_to_tool(tool, policy)

        self.assertEqual(getattr(tool, TOOL_SKILL_MODE_ATTR), "skill")
        self.assertEqual(getattr(tool, TOOL_SKILL_ID_ATTR), "mail")
        self.assertTrue(is_skill_tool(tool))
        self.assertEqual(get_tool_skill_id(tool), "mail")
