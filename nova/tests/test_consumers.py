import asyncio
import json
from importlib import import_module

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

                # Send ping from client, expect pong back
                await communicator.send_to(text_data=json.dumps({"type": "ping"}))
                msg = await communicator.receive_from()
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
                        "message": {"status": "RUNNING", "progress": 42},
                    },
                )

                # Client should receive the pushed update
                msg = await communicator.receive_from()
                data = json.loads(msg)
                self.assertEqual(data["status"], "RUNNING")
                self.assertEqual(data["progress"], 42)
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())

    def test_invalid_json_from_client(self):
        async def scenario():
            communicator, connected = await self._connect("/ws/task/err/")
            try:
                self.assertTrue(connected)

                # Send invalid JSON string
                await communicator.send_to(text_data="not a json")
                msg = await communicator.receive_from()
                data = json.loads(msg)
                self.assertIn("error", data)
                self.assertEqual(data["error"], "Invalid message")
            finally:
                await communicator.disconnect()

        asyncio.run(scenario())
