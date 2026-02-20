import asyncio
import json
from unittest import TestCase

from nova.llm.skill_runtime_tools import build_skill_control_tools


class SkillRuntimeToolsTests(TestCase):
    def test_list_skills_and_load_skill(self):
        catalog = {
            "mail": {
                "id": "mail",
                "label": "Mail",
                "tool_names": ["list_emails"],
                "instructions": ["Use preview mode first."],
            }
        }
        tools = build_skill_control_tools(catalog)
        self.assertEqual({tool.name for tool in tools}, {"list_skills", "load_skill"})

        list_tool = next(tool for tool in tools if tool.name == "list_skills")
        load_tool = next(tool for tool in tools if tool.name == "load_skill")

        listed = json.loads(asyncio.run(list_tool.coroutine()))
        self.assertEqual(listed["status"], "ok")
        self.assertEqual(listed["skills"][0]["id"], "mail")

        loaded = json.loads(asyncio.run(load_tool.coroutine(skill="mail")))
        self.assertEqual(loaded["status"], "loaded")
        self.assertEqual(loaded["skill"], "mail")

        unknown = json.loads(asyncio.run(load_tool.coroutine(skill="unknown")))
        self.assertEqual(unknown["status"], "error")
        self.assertEqual(unknown["error"], "unknown_skill")
