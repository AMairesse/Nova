# nova/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from nova.models import Task, TaskStatus

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

        # Send initial task state
        await self.send_initial_state()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.task_group_name,
            self.channel_name
        )

    # Receive message from WebSocket (handle ping/pong and optional client messages)
    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message_type = text_data_json.get('type')

        if message_type == 'ping':
            # Respond with pong for heartbeat
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return  # Exit early

        # Can handle other client messages here if needed (e.g., request refresh)
        pass

    # Receive message from room group (pushed from views/thread)
    async def task_update(self, event):
        message = event['message']
        # Send message to WebSocket
        await self.send(text_data=json.dumps(message))

    @database_sync_to_async
    def get_task_state(self):
        try:
            task = Task.objects.get(id=self.task_id, user=self.scope['user'])
            return {
                'status': task.status,
                'progress_logs': task.progress_logs,
                'result': task.result,
                'updated_at': task.updated_at.isoformat(),
                'is_completed': task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED],
            }
        except Task.DoesNotExist:
            return {'error': 'Task not found or access denied'}

    async def send_initial_state(self):
        state = await self.get_task_state()
        await self.send(text_data=json.dumps(state))
