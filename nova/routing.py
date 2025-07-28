# nova/routing.py
from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path('ws/task/<int:task_id>/', consumers.TaskProgressConsumer.as_asgi()),
]
