from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, call

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
