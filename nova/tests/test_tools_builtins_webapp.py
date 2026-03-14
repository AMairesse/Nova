from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from django.test import TestCase

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

    def test_get_skill_instructions_promote_agent_authored_code(self):
        instructions = webapp_tools.get_skill_instructions()
        rendered = "\n".join(str(i) for i in instructions)
        self.assertIn("generate the initial code yourself", rendered)
        self.assertIn("do not call it with empty files", rendered)


class WebAppBuiltinsBehaviorTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=1)
        self.thread = SimpleNamespace(id=10)
        self.agent = SimpleNamespace(user=self.user, thread=self.thread, _resources={})

    async def test_upsert_create_requires_name(self):
        result = await webapp_tools.upsert_webapp(
            None,
            {"index.html": "<h1>Hello</h1>"},
            self.agent,
            name="   ",
        )
        self.assertEqual(result, "Webapp name is required.")

    async def test_upsert_create_empty_files_returns_actionable_error(self):
        result = await webapp_tools.upsert_webapp(
            None,
            {},
            self.agent,
            name="Dashboard",
        )
        self.assertIn("generated source files", result)
        self.assertIn("call webapp_create again", result)

    async def test_upsert_create_requires_index_file(self):
        result = await webapp_tools.upsert_webapp(
            None,
            {"main.html": "<h1>Hello</h1>"},
            self.agent,
            name="Dashboard",
        )
        self.assertIn("requires an 'index.html' entry file", result)
        self.assertIn("include it in files", result)

    async def test_upsert_create_returns_name_and_public_url(self):
        fake_webapp = SimpleNamespace(slug="demo", name="Dashboard", thread_id=self.thread.id)
        with patch("nova.tools.builtins.webapp._create_webapp_sync", return_value=fake_webapp) as mocked_create:
            with patch("nova.tools.builtins.webapp._upsert_files", new_callable=AsyncMock, return_value=None):
                with patch("nova.tools.builtins.webapp._touch_webapp_sync", return_value=None):
                    with patch("nova.tools.builtins.webapp._get_webapp_by_slug_sync", return_value=fake_webapp):
                        with patch("nova.tools.builtins.webapp._publish_webapp_update", new_callable=AsyncMock):
                            result = await webapp_tools.upsert_webapp(
                                None,
                                {"index.html": "<h1>Hello</h1>"},
                                self.agent,
                                name="Dashboard",
                            )

        mocked_create.assert_called_once_with(self.user, self.thread, "Dashboard")
        self.assertEqual(result["slug"], "demo")
        self.assertEqual(result["name"], "Dashboard")
        self.assertTrue(result["public_url"].endswith("/apps/demo/"))

    async def test_upsert_update_can_rename(self):
        fake_webapp = SimpleNamespace(slug="demo", name="Old", thread_id=self.thread.id)
        with patch(
            "nova.tools.builtins.webapp._get_webapp_by_slug_sync",
            side_effect=[fake_webapp, fake_webapp],
        ):
            with patch("nova.tools.builtins.webapp._has_webapp_file_sync", return_value=True):
                with patch("nova.tools.builtins.webapp._save_webapp_sync", return_value=fake_webapp):
                    with patch("nova.tools.builtins.webapp._upsert_files", new_callable=AsyncMock, return_value=None):
                        with patch("nova.tools.builtins.webapp._touch_webapp_sync", return_value=None):
                            with patch("nova.tools.builtins.webapp._publish_webapp_update", new_callable=AsyncMock):
                                result = await webapp_tools.upsert_webapp(
                                    "demo",
                                    {"index.html": "<h1>Updated</h1>"},
                                    self.agent,
                                    name="Renamed",
                                )

        self.assertEqual(fake_webapp.name, "Renamed")
        self.assertEqual(result["slug"], "demo")
        self.assertEqual(result["name"], "Renamed")

    async def test_upsert_update_requires_index_for_legacy_webapp(self):
        fake_webapp = SimpleNamespace(slug="legacy", name="Old", thread_id=self.thread.id)
        with patch("nova.tools.builtins.webapp._get_webapp_by_slug_sync", return_value=fake_webapp):
            with patch("nova.tools.builtins.webapp._has_webapp_file_sync", return_value=False):
                result = await webapp_tools.upsert_webapp(
                    "legacy",
                    {"app.js": "console.log('x')"},
                    self.agent,
                    name="Legacy",
                )

        self.assertIn("has no 'index.html' entry file", result)
        self.assertIn("include it in this webapp_update call", result)

    async def test_list_webapps_returns_items(self):
        rows = [
            {"slug": "one", "name": "App One", "updated_at": datetime(2026, 3, 1, tzinfo=timezone.utc)},
            {"slug": "two", "name": "", "updated_at": None},
        ]
        with patch("nova.tools.builtins.webapp._list_thread_webapps_sync", return_value=rows):
            result = await webapp_tools.list_webapps(self.agent)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["name"], "App One")
        self.assertEqual(result["items"][1]["name"], "two")  # fallback to slug
        self.assertTrue(result["items"][0]["public_url"].endswith("/apps/one/"))

    async def test_delete_webapp_removes_and_publishes(self):
        fake_webapp = SimpleNamespace(slug="demo", thread_id=self.thread.id)
        with patch("nova.tools.builtins.webapp._get_webapp_by_slug_sync", return_value=fake_webapp):
            with patch("nova.tools.builtins.webapp._delete_webapp_sync", return_value=None) as mocked_delete:
                with patch("nova.tools.builtins.webapp.publish_webapps_update", new_callable=AsyncMock) as mocked_publish:
                    result = await webapp_tools.delete_webapp("demo", self.agent)

        mocked_delete.assert_called_once_with(fake_webapp)
        mocked_publish.assert_awaited_once_with(
            self.thread.id,
            "webapp_delete",
            slug="demo",
            channel_layer=None,
        )
        self.assertEqual(result, {"slug": "demo", "status": "deleted"})

    async def test_cross_thread_operations_are_rejected(self):
        foreign_webapp = SimpleNamespace(slug="foreign", thread_id=999)

        with patch("nova.tools.builtins.webapp._get_webapp_by_slug_sync", return_value=foreign_webapp):
            read_result = await webapp_tools.read_webapp("foreign", self.agent)
            update_result = await webapp_tools.upsert_webapp(
                "foreign",
                {"index.html": "<h1>X</h1>"},
                self.agent,
                name="Renamed",
            )
            with patch("nova.tools.builtins.webapp._delete_webapp_sync", return_value=None) as mocked_delete:
                delete_result = await webapp_tools.delete_webapp("foreign", self.agent)

        self.assertIn("different conversation", read_result)
        self.assertIn("different conversation", update_result)
        self.assertIn("different conversation", delete_result)
        mocked_delete.assert_not_called()

    async def test_get_functions_exposes_full_lifecycle_tools(self):
        tools = await webapp_tools.get_functions(None, self.agent)
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {"webapp_create", "webapp_update", "webapp_list", "webapp_read", "webapp_delete"},
        )

        create_tool = next(tool for tool in tools if tool.name == "webapp_create")
        create_schema = create_tool.args_schema
        self.assertIn("files", create_schema["required"])
        self.assertEqual(create_schema["properties"]["files"]["minProperties"], 1)


class WebAppBuiltinsRealtimeTests(IsolatedAsyncioTestCase):
    async def test_publish_webapp_update_emits_task_and_thread_events(self):
        fake_layer = AsyncMock()
        agent = SimpleNamespace(
            _resources={"task_id": "task-123", "channel_layer": fake_layer},
            thread=SimpleNamespace(id=77),
        )

        await webapp_tools._publish_webapp_update(
            agent,
            slug="demo",
            public_url="/apps/demo/",
            reason="webapp_create",
        )

        self.assertEqual(fake_layer.group_send.await_count, 3)
        fake_layer.group_send.assert_has_awaits([
            call(
                "task_task-123",
                {"type": "task_update", "message": {"type": "webapp_public_url", "slug": "demo", "public_url": "/apps/demo/"}},
            ),
            call(
                "task_task-123",
                {"type": "task_update", "message": {"type": "webapp_update", "slug": "demo"}},
            ),
            call(
                "thread_77_files",
                {"type": "webapps_update", "reason": "webapp_create", "slug": "demo"},
            ),
        ])
