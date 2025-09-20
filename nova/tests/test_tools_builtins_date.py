import re
import sys
import types
import importlib
import asyncio
from datetime import datetime
from django.test import SimpleTestCase


class DateBuiltinTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.mod = self._import_date_module_with_fakes()

    def tearDown(self):
        super().tearDown()
        sys.modules.pop("nova.tools.builtins.date", None)
        sys.modules.pop("langchain_core.tools", None)
        sys.modules.pop("nova.llm.llm_agent", None)

    def _import_date_module_with_fakes(self):
        # Fake langchain_core.tools.StructuredTool
        lc_core_tools = types.ModuleType("langchain_core.tools")

        class StructuredTool:
            @classmethod
            def from_function(cls, func=None, coroutine=None, name=None, description=None, args_schema=None):
                # Return a plain object capturing what was passed for assertions
                return {
                    "func": func,
                    "coroutine": coroutine,
                    "name": name,
                    "description": description,
                    "args_schema": args_schema,
                }

        lc_core_tools.StructuredTool = StructuredTool

        # Fake nova.llm_agent (only needed for type annotation import)
        fake_llm_agent = types.ModuleType("nova.llm.llm_agent")

        class LLMAgent:
            pass

        fake_llm_agent.LLMAgent = LLMAgent

        sys.modules["langchain_core.tools"] = lc_core_tools
        sys.modules["nova.llm.llm_agent"] = fake_llm_agent

        # Ensure a fresh import of the module under test
        sys.modules.pop("nova.tools.builtins.date", None)
        mod = importlib.import_module("nova.tools.builtins.date")
        return mod

    def test_pure_functions(self):
        # current_date format YYYY-MM-DD
        s = self.mod.current_date()
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2}$")

        # current_datetime format YYYY-MM-DD HH:MM:SS and parseable
        dt = self.mod.current_datetime()
        self.assertRegex(dt, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")  # should not raise

        # add_days
        self.assertEqual(self.mod.add_days("2024-01-31", 1), "2024-02-01")
        self.assertEqual(self.mod.add_days("2024-03-01", -1), "2024-02-29")  # leap year

        # add_weeks
        self.assertEqual(self.mod.add_weeks("2024-01-01", 1), "2024-01-08")
        self.assertEqual(self.mod.add_weeks("2024-01-08", -1), "2024-01-01")

        # count_days
        self.assertEqual(self.mod.count_days("2024-01-01", "2024-01-31"), 30)
        self.assertEqual(self.mod.count_days("2024-01-31", "2024-01-01"), -30)

    def test_get_functions_returns_five_structured_tools_with_expected_schema(self):
        class DummyAgent:
            pass

        tools = asyncio.run(self.mod.get_functions(tool=object(), agent=DummyAgent()))
        self.assertEqual(len(tools), 5)

        # Map by name for easier assertions
        by_name = {t["name"]: t for t in tools}
        expected_names = {"current_date", "current_datetime", "add_days", "add_weeks", "count_days"}
        self.assertEqual(set(by_name.keys()), expected_names)

        # current_date
        t = by_name["current_date"]
        self.assertEqual(t["description"], "Return the current date for GMT (format: YYYY-MM-DD)")
        self.assertEqual(t["args_schema"], {"type": "object", "properties": {}, "required": []})
        self.assertIs(t["func"], self.mod.current_date)

        # current_datetime
        t = by_name["current_datetime"]
        self.assertEqual(t["description"], "Return the current date and time for GMT (format: YYYY-MM-DD HH:MM:SS)")
        self.assertEqual(t["args_schema"], {"type": "object", "properties": {}, "required": []})
        self.assertIs(t["func"], self.mod.current_datetime)

        # add_days
        t = by_name["add_days"]
        self.assertEqual(t["description"], "Add N days to the provided date")
        schema = t["args_schema"]
        self.assertEqual(schema.get("type"), "object")
        self.assertIn("date", schema.get("properties", {}))
        self.assertIn("days", schema.get("properties", {}))
        self.assertIn("date", schema.get("required", []))
        self.assertIn("days", schema.get("required", []))
        self.assertEqual(schema["properties"]["days"]["type"], "integer")
        self.assertEqual(schema["properties"]["date"]["type"], "string")
        self.assertTrue(re.match(r"^\^\\d\{4\}-\\d\{2\}-\\d\{2\}\$$", schema["properties"]["date"]["pattern"]))
        self.assertIs(t["func"], self.mod.add_days)

        # add_weeks
        t = by_name["add_weeks"]
        self.assertEqual(t["description"], "Add N weeks to the provided date")
        schema = t["args_schema"]
        self.assertIn("weeks", schema.get("properties", {}))
        self.assertIn("date", schema.get("required", []))
        self.assertIn("weeks", schema.get("required", []))
        self.assertEqual(schema["properties"]["weeks"]["type"], "integer")
        self.assertIs(t["func"], self.mod.add_weeks)

        # count_days
        t = by_name["count_days"]
        self.assertEqual(t["description"], "Count the number of days between two dates")
        schema = t["args_schema"]
        self.assertIn("start_date", schema.get("properties", {}))
        self.assertIn("end_date", schema.get("properties", {}))
        self.assertIn("start_date", schema.get("required", []))
        self.assertIn("end_date", schema.get("required", []))
        self.assertEqual(schema["properties"]["start_date"]["type"], "string")
        self.assertEqual(schema["properties"]["end_date"]["type"], "string")
        self.assertIs(t["func"], self.mod.count_days)
