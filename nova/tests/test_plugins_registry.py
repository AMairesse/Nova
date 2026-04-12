from django.test import SimpleTestCase

from nova.plugins.registry import (
    get_internal_plugins,
    get_plugin,
    get_plugin_for_builtin_subtype,
)
from nova.plugins.builtins import get_available_tool_types


class InternalPluginRegistryTests(SimpleTestCase):
    def test_each_builtin_subtype_maps_to_a_single_plugin(self):
        seen: dict[str, str] = {}
        for plugin in get_internal_plugins():
            for subtype in plugin.builtin_subtypes:
                self.assertNotIn(subtype, seen)
                seen[subtype] = plugin.plugin_id

        self.assertEqual(get_plugin_for_builtin_subtype("memory").plugin_id, "memory")
        self.assertEqual(get_plugin_for_builtin_subtype("email").plugin_id, "mail")
        self.assertEqual(get_plugin_for_builtin_subtype("code_execution").plugin_id, "python")

    def test_system_plugins_do_not_depend_on_tool_rows(self):
        terminal = get_plugin("terminal")
        history = get_plugin("history")

        self.assertEqual(terminal.kind, "system")
        self.assertFalse(terminal.builtin_subtypes)
        self.assertEqual(history.kind, "system")
        self.assertFalse(history.builtin_subtypes)

    def test_builtin_tool_listing_is_driven_by_plugin_registry(self):
        tool_types = get_available_tool_types()

        self.assertIn("date", tool_types)
        self.assertIn("browser", tool_types)
        self.assertIn("memory", tool_types)
        self.assertIn("webapp", tool_types)
        self.assertEqual(tool_types["memory"]["python_path"], "nova.plugins.memory")
