import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from channels.testing import WebsocketCommunicator

from nova.asgi import application
from nova.consumers import FileProgressConsumer, TaskProgressConsumer


class TaskProgressConsumerTests(IsolatedAsyncioTestCase):
    async def test_connect_and_disconnect_manage_group_membership(self):
        consumer = TaskProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"task_id": "123"}},
            "user": SimpleNamespace(is_authenticated=True, id=7),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-1"
        consumer.accept = AsyncMock()
        consumer._user_can_access_task = AsyncMock(return_value=True)

        await consumer.connect()
        await consumer.disconnect(1000)

        self.assertEqual(consumer.task_group_name, "task_123")
        consumer.channel_layer.group_add.assert_awaited_once_with("task_123", "channel-1")
        consumer.accept.assert_awaited_once()
        consumer.channel_layer.group_discard.assert_awaited_once_with("task_123", "channel-1")

    async def test_connect_rejects_anonymous_users(self):
        consumer = TaskProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"task_id": "123"}},
            "user": SimpleNamespace(is_authenticated=False, id=None),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-1"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()

        await consumer.connect()

        consumer.close.assert_awaited_once_with(code=4403)
        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()

    async def test_connect_rejects_non_owner(self):
        consumer = TaskProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"task_id": "123"}},
            "user": SimpleNamespace(is_authenticated=True, id=7),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-1"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._user_can_access_task = AsyncMock(return_value=False)

        await consumer.connect()

        consumer.close.assert_awaited_once_with(code=4403)
        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()

    async def test_receive_rejects_large_and_invalid_payloads_and_handles_ping(self):
        consumer = TaskProgressConsumer()
        consumer.task_id = "task-1"
        consumer.send = AsyncMock()

        await consumer.receive("x" * 1025)
        await consumer.receive("{bad-json")
        await consumer.receive(json.dumps({"type": "ping"}))

        self.assertEqual(consumer.send.await_count, 3)
        too_large = json.loads(consumer.send.await_args_list[0].kwargs["text_data"])
        invalid = json.loads(consumer.send.await_args_list[1].kwargs["text_data"])
        pong = json.loads(consumer.send.await_args_list[2].kwargs["text_data"])
        self.assertEqual(too_large, {"error": "Message too large"})
        self.assertEqual(invalid, {"error": "Invalid message"})
        self.assertEqual(pong, {"type": "pong"})

    async def test_task_update_relays_group_messages(self):
        consumer = TaskProgressConsumer()
        consumer.send = AsyncMock()

        await consumer.task_update({"message": {"type": "response_chunk", "chunk": "hi"}})

        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload["type"], "response_chunk")
        self.assertEqual(payload["chunk"], "hi")


class FileProgressConsumerTests(IsolatedAsyncioTestCase):
    async def test_connect_and_disconnect_manage_thread_group(self):
        consumer = FileProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"thread_id": "77"}},
            "user": SimpleNamespace(is_authenticated=True, id=7),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-2"
        consumer.accept = AsyncMock()
        consumer._user_can_access_thread = AsyncMock(return_value=True)

        await consumer.connect()
        await consumer.disconnect(1000)

        self.assertEqual(consumer.group_name, "thread_77_files")
        consumer.channel_layer.group_add.assert_awaited_once_with("thread_77_files", "channel-2")
        consumer.accept.assert_awaited_once()
        consumer.channel_layer.group_discard.assert_awaited_once_with("thread_77_files", "channel-2")

    async def test_connect_rejects_anonymous_users(self):
        consumer = FileProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"thread_id": "77"}},
            "user": SimpleNamespace(is_authenticated=False, id=None),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-2"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()

        await consumer.connect()

        consumer.close.assert_awaited_once_with(code=4403)
        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()

    async def test_connect_rejects_non_owner(self):
        consumer = FileProgressConsumer()
        consumer.scope = {
            "url_route": {"kwargs": {"thread_id": "77"}},
            "user": SimpleNamespace(is_authenticated=True, id=7),
        }
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "channel-2"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer._user_can_access_thread = AsyncMock(return_value=False)

        await consumer.connect()

        consumer.close.assert_awaited_once_with(code=4403)
        consumer.channel_layer.group_add.assert_not_awaited()
        consumer.accept.assert_not_awaited()

    async def test_receive_handles_ping_and_logs_other_messages(self):
        consumer = FileProgressConsumer()
        consumer.thread_id = "88"
        consumer.send = AsyncMock()

        await consumer.receive('{"type":"ping"}')
        with patch("nova.consumers.logger.info") as mocked_info:
            await consumer.receive("client-side notice")

        pong = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(pong, {"type": "pong"})
        mocked_info.assert_called_once()

    async def test_file_progress_relays_progress_payload(self):
        consumer = FileProgressConsumer()
        consumer.send = AsyncMock()

        await consumer.file_progress({"progress": 42})

        payload = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(payload, {"type": "progress", "progress": 42})


class WebSocketOriginValidatorTests(IsolatedAsyncioTestCase):
    async def test_disallowed_origin_is_blocked_during_handshake(self):
        communicator = WebsocketCommunicator(
            application,
            "/ws/task/1/",
            headers=[(b"origin", b"http://evil.example")],
        )

        connected, _subprotocol = await communicator.connect()

        self.assertFalse(connected)
