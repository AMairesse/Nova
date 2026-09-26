from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from nova.consumers import FileProgressConsumer
from nova.realtime.sidebar_updates import publish_file_update, publish_webapps_update
from nova.tasks.tasks import _publish_thread_subject_update


class SidebarRealtimePublishTests(IsolatedAsyncioTestCase):
    async def test_publish_file_update_sends_group_message(self):
        fake_layer = AsyncMock()

        await publish_file_update(42, "file_create", channel_layer=fake_layer)

        fake_layer.group_send.assert_awaited_once_with(
            "thread_42_files",
            {"type": "file_update", "reason": "file_create"},
        )

    async def test_publish_webapps_update_sends_group_message_with_slug(self):
        fake_layer = AsyncMock()

        await publish_webapps_update(24, "webapp_update", slug="demo", channel_layer=fake_layer)

        fake_layer.group_send.assert_awaited_once_with(
            "thread_24_files",
            {"type": "webapps_update", "reason": "webapp_update", "slug": "demo"},
        )

    async def test_publish_webapps_update_supports_delete_reason(self):
        fake_layer = AsyncMock()

        await publish_webapps_update(24, "webapp_delete", slug="demo", channel_layer=fake_layer)

        fake_layer.group_send.assert_awaited_once_with(
            "thread_24_files",
            {"type": "webapps_update", "reason": "webapp_delete", "slug": "demo"},
        )

    async def test_publish_ignores_missing_thread_id(self):
        fake_layer = AsyncMock()

        await publish_file_update(None, "upload", channel_layer=fake_layer)
        await publish_webapps_update(None, "webapp_update", channel_layer=fake_layer)

        fake_layer.group_send.assert_not_awaited()

    async def test_publish_uses_default_channel_layer_when_not_provided(self):
        fake_layer = AsyncMock()
        with patch("nova.realtime.sidebar_updates.get_channel_layer", return_value=fake_layer):
            await publish_file_update(7, "upload")

        fake_layer.group_send.assert_awaited_once_with(
            "thread_7_files",
            {"type": "file_update", "reason": "upload"},
        )

    @patch("nova.tasks.tasks.async_to_sync")
    @patch("nova.tasks.tasks.get_channel_layer")
    def test_thread_subject_update_reaches_task_and_file_groups(self, get_layer, async_to_sync_mock):
        get_layer.return_value = AsyncMock()
        group_send = MagicMock()
        async_to_sync_mock.return_value = group_send

        _publish_thread_subject_update(9, 42, "New title")

        self.assertEqual(group_send.call_count, 2)
        self.assertEqual(group_send.call_args_list[0].args[0], "task_9")
        self.assertEqual(group_send.call_args_list[1].args[0], "thread_42_files")


class FileProgressConsumerRealtimeTests(IsolatedAsyncioTestCase):
    async def test_file_update_relay(self):
        consumer = FileProgressConsumer()
        consumer.send = AsyncMock()

        await consumer.file_update({"reason": "file_delete"})

        consumer.send.assert_awaited_once()
        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload["type"], "file_update")
        self.assertEqual(payload["reason"], "file_delete")

    async def test_webapps_update_relay(self):
        consumer = FileProgressConsumer()
        consumer.send = AsyncMock()

        await consumer.webapps_update({"reason": "webapp_create", "slug": "app-1"})

        consumer.send.assert_awaited_once()
        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload["type"], "webapps_update")
        self.assertEqual(payload["reason"], "webapp_create")
        self.assertEqual(payload["slug"], "app-1")

    async def test_thread_subject_update_relay(self):
        consumer = FileProgressConsumer()
        consumer.send = AsyncMock()

        await consumer.thread_subject_updated({
            "thread_id": 42,
            "thread_subject": "New title",
        })

        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload, {
            "type": "thread_subject_updated",
            "thread_id": 42,
            "thread_subject": "New title",
        })
