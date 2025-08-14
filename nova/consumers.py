# nova/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from nova.models import Task, TaskStatus
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

    # Receive message from WebSocket (handle ping/pong and optional client messages)
    async def receive(self, text_data):
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type')

            if message_type == 'ping':
                # Respond with pong for heartbeat
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return  # Exit early

            # Can handle other client messages here if needed (e.g., request refresh)
            pass
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in receive: {e}")
            await self.send(text_data=json.dumps({'error': 'Invalid message'}))

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
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Client can send upload start signals if needed; for now, server broadcasts progress
        pass

    async def file_progress(self, event):
        progress = event['progress']
        await self.send(text_data=json.dumps({'type': 'progress', 'progress': progress}))