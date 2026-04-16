from django.test import SimpleTestCase

from nova.runtime.commands.shell import (
    merge_command_outputs,
    resolve_boolean_command_status,
    should_execute_segment,
)


class RuntimeShellHelpersTests(SimpleTestCase):
    def test_merge_command_outputs_preserves_line_boundaries(self):
        self.assertEqual(
            merge_command_outputs(["alpha", "beta\n", "gamma"]),
            "alpha\nbeta\ngamma",
        )

    def test_should_execute_segment_matches_shell_truth_table(self):
        self.assertTrue(should_execute_segment(None, 1))
        self.assertTrue(should_execute_segment(";", 1))
        self.assertTrue(should_execute_segment("&&", 0))
        self.assertFalse(should_execute_segment("&&", 1))
        self.assertTrue(should_execute_segment("||", 1))
        self.assertFalse(should_execute_segment("||", 0))

    def test_resolve_boolean_command_status(self):
        self.assertEqual(resolve_boolean_command_status("true"), 0)
        self.assertEqual(resolve_boolean_command_status("false"), 1)
        self.assertIsNone(resolve_boolean_command_status("echo"))
