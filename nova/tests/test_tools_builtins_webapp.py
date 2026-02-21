from unittest import TestCase

from nova.tools.builtins import webapp as webapp_tools


class WebAppBuiltinsTests(TestCase):
    def test_metadata_marks_webapp_as_skill(self):
        loading = (webapp_tools.METADATA or {}).get("loading", {})
        self.assertEqual(loading.get("mode"), "skill")
        self.assertEqual(loading.get("skill_id"), "webapp")
        self.assertEqual(loading.get("skill_label"), "WebApp")

    def test_get_skill_instructions_returns_non_empty_list(self):
        instructions = webapp_tools.get_skill_instructions()
        self.assertIsInstance(instructions, list)
        self.assertTrue(any(str(i).strip() for i in instructions))
