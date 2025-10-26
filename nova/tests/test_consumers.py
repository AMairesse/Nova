import asyncio
import json
from importlib import import_module
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from channels.testing import WebsocketCommunicator
from channels.layers import get_channel_layer


@override_settings(
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
)
class TaskProgressConsumerTests(SimpleTestCase):
    async def _connect(self, path="/ws/task/abc/"):
        # Import the ASGI application defined by your project
        asgi_app = import_module("nova.asgi").application
        communicator = WebsocketCommunicator(asgi_app, path)
        connected, _ = await communicator.connect()
        return communicator, connected

    def test_connect_and_pong(self):
        async def scenario():
            communicator, connected = await self._connect("/ws/task/123/")
            try:
                self.assertTrue(connected)

                # Test optimized ping (using startswith for efficiency)
                await communicator.send_to(text_data='{"type":"ping"}')
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertEqual(data, {"type": "pong"})
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    def test_task_update_broadcast(self):
        async def scenario():
            communicator, connected = await self._connect("/ws/task/xyz/")
            try:
                self.assertTrue(connected)

                # Send a group message as the server would do
                layer = get_channel_layer()
                await layer.group_send(
                    "task_xyz",
                    {
                        "type": "task_update",
                        "message": {"type": "update", "status": "RUNNING",
                                    "progress": 42},
                    },
                )

                # Client should receive the pushed update
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertEqual(data.get("type"), "update")
                self.assertEqual(data["status"], "RUNNING")
                self.assertEqual(data["progress"], 42)
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    @patch('nova.consumers.logger')
    def test_invalid_json_from_client(self, mock_logger):
        async def scenario():
            communicator, connected = await self._connect("/ws/task/err/")
            try:
                self.assertTrue(connected)

                # Send invalid JSON string
                await communicator.send_to(text_data="not a json")
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertIn("error", data)
                self.assertEqual(data["error"], "Invalid message")
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    @patch('nova.consumers.logger')
    def test_message_too_large(self, mock_logger):
        async def scenario():
            communicator, connected = await self._connect("/ws/task/big/")
            try:
                self.assertTrue(connected)

                # Send oversized message (over 1024 bytes limit in consumer)
                large_msg = json.dumps({"type": "ping", "data": "x" * 2000})
                await communicator.send_to(text_data=large_msg)
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertIn("error", data)
                self.assertEqual(data["error"], "Message too large")
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())


@override_settings(
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
)
class FileProgressConsumerTests(SimpleTestCase):
    async def _connect(self, path="/ws/files/456/"):
        asgi_app = import_module("nova.asgi").application
        communicator = WebsocketCommunicator(asgi_app, path)
        connected, _ = await communicator.connect()
        return communicator, connected

    def test_connect_and_pong(self):
        async def scenario():
            communicator, connected = await self._connect("/ws/files/789/")
            try:
                self.assertTrue(connected)

                # Test ping/pong (similar optimization as Task consumer)
                await communicator.send_to(text_data='{"type":"ping"}')
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertEqual(data, {"type": "pong"})
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    def test_file_progress_broadcast(self):
        async def scenario():
            communicator, connected = await self._connect("/ws/files/101/")
            try:
                self.assertTrue(connected)

                # Simulate server-side broadcast (e.g., from file upload)
                layer = get_channel_layer()
                await layer.group_send(
                    "thread_101_files",
                    {
                        "type": "file_progress",
                        "progress": {"percentage": 75, "status": "uploading"},
                    },
                )

                # Client should receive the progress update
                msg = await communicator.receive_from(timeout=5)
                data = json.loads(msg)
                self.assertEqual(data["type"], "progress")
                self.assertEqual(data["progress"]["percentage"], 75)
                self.assertEqual(data["progress"]["status"], "uploading")
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    @patch('nova.consumers.logger')
    def test_unknown_client_message(self, mock_logger):
        async def scenario():
            communicator, connected = await self._connect("/ws/files/999/")
            try:
                self.assertTrue(connected)

                # Send unknown message; consumer logs but doesn't respond/error
                await communicator.send_to(text_data=json.dumps({"type":
                                                                 "unknown"}))
                # No response expected; just check no crash
                # (use timeout to confirm silence)
                with self.assertRaises(asyncio.TimeoutError):
                    await communicator.receive_from(timeout=1)
            finally:
                try:
                    await communicator.disconnect()
                except asyncio.exceptions.CancelledError:
                    pass

        asyncio.run(scenario())
