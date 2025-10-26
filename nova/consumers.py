# nova/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
import logging

logger = logging.getLogger(__name__)


class TaskProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.task_id = self.scope['url_route']['kwargs']['task_id']
        self.task_group_name = f'task_{self.task_id}'

        # Join room group
        await self.channel_layer.group_add(
            self.task_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.task_group_name,
            self.channel_name
        )

    # Receive message from WebSocket
    # (handle ping/pong and optional client messages)
    async def receive(self, text_data):
        if len(text_data) > 1024:
            await self.send(text_data=json.dumps({'error': 'Message too large'}))
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'error': 'Invalid message'}))
            return

        if data.get('type') == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            logger.debug("Ping-pong handled for task %s", self.task_id)
            return

    # Receive message from room group (pushed from views/thread)
    async def task_update(self, event):
        message = event['message']
        # Send message to WebSocket
        await self.send(text_data=json.dumps(message))


class FileProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.thread_id = self.scope['url_route']['kwargs']['thread_id']
        self.group_name = f"thread_{self.thread_id}_files"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name,
                                               self.channel_name)

    async def receive(self, text_data):
        # Early ping handling similar to TaskProgressConsumer
        if text_data.startswith('{"type":"ping"}'):
            await self.send(text_data=json.dumps({'type': 'pong'}))
            logger.debug(f"Ping-pong handled for thread {self.thread_id}")
            return

        # Client can send upload start signals if needed; for now, log unknowns
        logger.info(f"Received client message for thread {self.thread_id}: {text_data[:100]}")

    async def file_progress(self, event):
        progress = event['progress']
        await self.send(text_data=json.dumps({'type': 'progress', 'progress': progress}))
