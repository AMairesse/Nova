from unittest import IsolatedAsyncioTestCase

from django.test import TestCase

from nova.tools.builtins import webapp as webapp_tools


class WebAppBuiltinsTests(TestCase):
    def test_metadata_marks_webapp_as_skill(self):
        loading = (webapp_tools.METADATA or {}).get("loading", {})
        self.assertEqual(loading.get("mode"), "skill")
        self.assertEqual(loading.get("skill_id"), "webapp")
        self.assertEqual(loading.get("skill_label"), "WebApp")

    def test_get_skill_instructions_describe_terminal_native_flow(self):
        instructions = webapp_tools.get_skill_instructions()
        rendered = "\n".join(str(item) for item in instructions)

        self.assertIsInstance(instructions, list)
        self.assertIn("terminal", rendered.lower())
        self.assertIn("webapp expose", rendered)


class WebAppBuiltinsAsyncTests(IsolatedAsyncioTestCase):
    async def test_get_functions_returns_no_legacy_tools(self):
        tools = await webapp_tools.get_functions(None, agent=None)
        self.assertEqual(tools, [])
