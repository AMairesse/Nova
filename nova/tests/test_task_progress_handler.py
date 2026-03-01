from uuid import UUID
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings

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

    @override_settings(WEBPUSH_ENABLED=True)
    async def test_on_task_complete_enqueues_webpush_notification(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(
            task_id=456,
            channel_layer=channel_layer,
            user_id=9,
            thread_id=12,
            thread_mode="thread",
        )

        with patch("nova.tasks.notification_tasks.send_task_webpush_notification.delay") as mocked_delay:
            await handler.on_task_complete("ok", 12, "Subject")

        mocked_delay.assert_called_once_with(
            user_id=9,
            task_id="456",
            thread_id=12,
            thread_mode="thread",
            status="completed",
        )

    async def test_streaming_flushes_on_sentence_boundary_and_llm_end(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=789, channel_layer=channel_layer)
        handler._stream_flush_interval_seconds = 60
        handler.on_chunk = AsyncMock()

        with patch("nova.tasks.TaskProgressHandler.markdown_to_html", side_effect=lambda text: f"<p>{text}</p>"):
            await handler.on_llm_new_token("Hello", run_id=UUID(int=1))
            await handler.on_llm_new_token(" world", run_id=UUID(int=1))
            handler.on_chunk.assert_not_awaited()

            await handler.on_llm_new_token(".", run_id=UUID(int=1))
            self.assertEqual(handler.on_chunk.await_count, 1)
            self.assertIn("Hello world.", handler.on_chunk.await_args_list[0].args[0])

            await handler.on_llm_new_token(" Next", run_id=UUID(int=1))
            self.assertEqual(handler.on_chunk.await_count, 1)

            await handler.on_llm_end(response={}, run_id=UUID(int=1))
            self.assertEqual(handler.on_chunk.await_count, 2)
            self.assertIn("Hello world. Next", handler.on_chunk.await_args_list[1].args[0])
