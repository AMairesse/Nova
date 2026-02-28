from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from nova.tasks.TaskProgressHandler import TaskProgressHandler


class TaskProgressHandlerTests(IsolatedAsyncioTestCase):
    async def test_on_error_publishes_message_payload(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=123, channel_layer=channel_layer)

        await handler.on_error("system_error: boom", "system_error")

        channel_layer.group_send.assert_awaited_once()
        args = channel_layer.group_send.await_args.args
        self.assertEqual(args[0], "task_123")
        payload = args[1]["message"]
        self.assertEqual(payload["type"], "task_error")
        self.assertEqual(payload["message"], "system_error: boom")
        self.assertEqual(payload["category"], "system_error")
        self.assertNotIn("error", payload)
