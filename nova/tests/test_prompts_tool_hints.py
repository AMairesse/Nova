from types import SimpleNamespace
from unittest import TestCase

from nova.llm.prompts import _get_tool_prompt_hints


class PromptToolHintsTests(TestCase):
    def test_get_tool_prompt_hints_deduplicates_and_strips(self):
        ctx = SimpleNamespace(tool_prompt_hints=["  hint-a  ", "hint-a", "", "hint-b"])

        out = _get_tool_prompt_hints(ctx)

        self.assertEqual(out, ["hint-a", "hint-b"])
