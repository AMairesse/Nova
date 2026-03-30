from uuid import UUID
from unittest import IsolatedAsyncioTestCase
from unittest.mock import ANY, AsyncMock, Mock, patch

from django.test import override_settings

from nova.tasks.TaskProgressHandler import TaskProgressHandler


class TaskProgressHandlerTests(IsolatedAsyncioTestCase):
    async def test_handler_initializes_with_previous_streamed_markdown(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(
            task_id=1,
            channel_layer=channel_layer,
            initial_streamed_markdown="Previous content",
        )

        self.assertEqual(handler.get_streamed_markdown(), "Previous content")

    async def test_on_error_publishes_message_payload(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=123, channel_layer=channel_layer)
        handler._persist_stream_state = AsyncMock()

        await handler.on_error("system_error: boom", "system_error")

        channel_layer.group_send.assert_awaited_once()
        args = channel_layer.group_send.await_args.args
        self.assertEqual(args[0], "task_123")
        payload = args[1]["message"]
        self.assertEqual(payload["type"], "task_error")
        self.assertEqual(payload["message"], "system_error: boom")
        self.assertEqual(payload["category"], "system_error")
        self.assertNotIn("error", payload)

    async def test_persist_stream_state_updates_task_html_and_markdown(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=321, channel_layer=channel_layer)
        handler.final_chunks = ["Hello", " world"]
        fake_qs = Mock()
        fake_qs.update = Mock(return_value=1)

        with patch("nova.models.Task.Task.objects.filter", return_value=fake_qs) as mocked_filter:
            await handler._persist_stream_state("<p>Hello world</p>")

        mocked_filter.assert_called_once_with(id=321)
        fake_qs.update.assert_called_once_with(
            current_response="<p>Hello world</p>",
            streamed_markdown="Hello world",
            updated_at=ANY,
        )

    async def test_on_progress_touches_runtime_heartbeat(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=654, channel_layer=channel_layer)
        fake_qs = Mock()
        fake_qs.update = Mock(return_value=1)

        with patch("nova.models.Task.Task.objects.filter", return_value=fake_qs) as mocked_filter:
            await handler.on_progress("Agent started")

        mocked_filter.assert_called_once_with(id=654)
        fake_qs.update.assert_called_once_with(updated_at=ANY)

    async def test_on_interrupt_flushes_and_persists_before_prompt(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=654, channel_layer=channel_layer)
        handler._flush_stream_chunk = AsyncMock(return_value="<p>chunk</p>")
        handler._persist_stream_state = AsyncMock()

        await handler.on_interrupt(10, "Question?", {"type": "object"}, "Planner")

        handler._persist_stream_state.assert_awaited_once_with("<p>chunk</p>")
        channel_layer.group_send.assert_awaited_once()
        payload = channel_layer.group_send.await_args.args[1]["message"]
        self.assertEqual(payload["type"], "user_prompt")

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

    @override_settings(WEBPUSH_ENABLED=True)
    async def test_on_task_complete_skips_webpush_notification_when_disabled(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(
            task_id=457,
            channel_layer=channel_layer,
            user_id=9,
            thread_id=12,
            thread_mode="thread",
            push_notifications_enabled=False,
        )

        with patch("nova.tasks.notification_tasks.send_task_webpush_notification.delay") as mocked_delay:
            await handler.on_task_complete("ok", 12, "Subject")

        mocked_delay.assert_not_called()

    @override_settings(WEBPUSH_ENABLED=True)
    async def test_on_error_skips_webpush_notification_when_disabled(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(
            task_id=458,
            channel_layer=channel_layer,
            user_id=9,
            thread_id=12,
            thread_mode="thread",
            push_notifications_enabled=False,
        )
        handler._flush_stream_chunk = AsyncMock(return_value=None)
        handler._persist_stream_state = AsyncMock()

        with patch("nova.tasks.notification_tasks.send_task_webpush_notification.delay") as mocked_delay:
            await handler.on_error("system_error: boom", "system_error")

        mocked_delay.assert_not_called()

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

    async def test_on_llm_end_persists_stream_state(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=777, channel_layer=channel_layer)
        handler._flush_stream_chunk = AsyncMock(return_value="<p>Done</p>")
        handler._persist_stream_state = AsyncMock()

        await handler.on_llm_end(response={}, run_id=UUID(int=1))

        handler._flush_stream_chunk.assert_awaited_once()
        handler._persist_stream_state.assert_awaited_once_with("<p>Done</p>")

    async def test_inserts_line_break_between_segments_after_tool_call(self):
        channel_layer = AsyncMock()
        handler = TaskProgressHandler(task_id=778, channel_layer=channel_layer)
        handler._stream_flush_interval_seconds = 60

        await handler.on_llm_new_token("First segment.", run_id=UUID(int=1))
        await handler.on_tool_start({"name": "webapp_create"}, "{}", run_id=UUID(int=2))
        await handler.on_tool_end("ok", run_id=UUID(int=3))
        await handler.on_llm_new_token("Second segment.", run_id=UUID(int=4))

        self.assertIn("First segment.\n\nSecond segment.", handler.get_streamed_markdown())
