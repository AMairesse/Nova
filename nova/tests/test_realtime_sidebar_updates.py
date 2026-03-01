from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from nova.consumers import FileProgressConsumer
from nova.realtime.sidebar_updates import publish_file_update, publish_webapps_update


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

