from asgiref.sync import async_to_sync

from nova.models.WebApp import WebApp
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import TerminalCommandError

from .runtime_command_base import _FakeChannelLayer, TerminalExecutorCommandTestCase


class WebappCommandTests(TerminalExecutorCommandTestCase):
    def test_webapp_commands_expose_show_list_and_delete_live_apps(self):
        webapp_tool = self._create_webapp_tool()
        executor = self._build_executor(
            TerminalCapabilities(webapp_tool=webapp_tool)
        )
        channel_layer = _FakeChannelLayer()
        executor.realtime_task_id = "task-123"
        executor.realtime_channel_layer = channel_layer

        async_to_sync(executor.execute)("mkdir /webapps")
        async_to_sync(executor.execute)("mkdir /webapps/demo")
        async_to_sync(executor.execute)('tee /webapps/demo/index.html --text "hello"')
        async_to_sync(executor.execute)('tee /webapps/demo/styles.css --text "body { color: red; }"')

        exposed = async_to_sync(executor.execute)('webapp expose /webapps/demo --name "Demo App"')
        webapp = WebApp.objects.get(thread=self.thread)
        listed = async_to_sync(executor.execute)("webapp list")
        shown = async_to_sync(executor.execute)(f"webapp show {webapp.slug}")

        self.assertIn("Exposed webapp", exposed)
        self.assertIn(webapp.slug, exposed)
        self.assertIn("Demo App", listed)
        self.assertIn(webapp.slug, shown)
        self.assertIn("source_root=/webapps/demo", shown)
        self.assertIn("entry_path=index.html", shown)

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)(f"webapp delete {webapp.slug}")

        deleted = async_to_sync(executor.execute)(f"webapp delete {webapp.slug} --confirm")
        self.assertEqual(deleted, f"Deleted webapp {webapp.slug}")
        self.assertFalse(WebApp.objects.filter(id=webapp.id).exists())

        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertIn("webapp_public_url", event_types)
        self.assertIn("webapp_update", event_types)
        self.assertIn("webapps_update", event_types)

    def test_webapp_file_mutation_and_root_move_refresh_live_binding(self):
        webapp_tool = self._create_webapp_tool()
        executor = self._build_executor(
            TerminalCapabilities(webapp_tool=webapp_tool)
        )
        channel_layer = _FakeChannelLayer()
        executor.realtime_task_id = "task-456"
        executor.realtime_channel_layer = channel_layer

        async_to_sync(executor.execute)("mkdir /webapps")
        async_to_sync(executor.execute)("mkdir /webapps/demo")
        async_to_sync(executor.execute)('tee /webapps/demo/index.html --text "hello"')
        async_to_sync(executor.execute)("webapp expose /webapps/demo")
        webapp = WebApp.objects.get(thread=self.thread)

        channel_layer.messages.clear()
        async_to_sync(executor.execute)('tee /webapps/demo/index.html --text "updated"')
        self.assertTrue(any(item["message"]["type"] == "webapp_update" for item in channel_layer.messages))

        channel_layer.messages.clear()
        async_to_sync(executor.execute)("mkdir /sites")
        moved = async_to_sync(executor.execute)("mv /webapps/demo /sites")

        webapp.refresh_from_db()
        self.assertEqual(moved, "Moved to /sites/demo")
        self.assertEqual(webapp.source_root, "/sites/demo")
        self.assertTrue(any(item["message"]["type"] == "webapp_update" for item in channel_layer.messages))

    def test_webapp_expose_rejects_escaped_html_entry(self):
        webapp_tool = self._create_webapp_tool()
        executor = self._build_executor(
            TerminalCapabilities(webapp_tool=webapp_tool)
        )

        async_to_sync(executor.execute)("mkdir /webapps")
        async_to_sync(executor.execute)("mkdir /webapps/demo")
        async_to_sync(executor.execute)('tee /webapps/demo/index.html --text "&lt;!DOCTYPE html&gt;&lt;html&gt;"')

        with self.assertRaises(TerminalCommandError) as escaped_error:
            async_to_sync(executor.execute)("webapp expose /webapps/demo")

        self.assertIn("Entry HTML appears escaped", str(escaped_error.exception))

    def test_recursive_webapp_root_deletion_auto_dereferences_publication(self):
        webapp_tool = self._create_webapp_tool()
        executor = self._build_executor(
            TerminalCapabilities(webapp_tool=webapp_tool)
        )
        channel_layer = _FakeChannelLayer()
        executor.realtime_task_id = "task-789"
        executor.realtime_channel_layer = channel_layer

        async_to_sync(executor.execute)("mkdir /webapps")
        async_to_sync(executor.execute)("mkdir /webapps/demo")
        async_to_sync(executor.execute)('tee /webapps/demo/index.html --text "<!doctype html><html></html>"')
        async_to_sync(executor.execute)("webapp expose /webapps/demo")
        webapp = WebApp.objects.get(thread=self.thread)

        channel_layer.messages.clear()
        removed = async_to_sync(executor.execute)("rm -rf /webapps/demo")

        self.assertEqual(removed, "Removed /webapps/demo")
        self.assertFalse(WebApp.objects.filter(id=webapp.id).exists())
        self.assertTrue(
            any(item["message"]["type"] == "webapps_update" and item["message"]["reason"] == "webapp_delete"
                for item in channel_layer.messages)
        )
