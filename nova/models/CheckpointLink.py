# nova/models/CheckpointLink.py
import uuid

from django.db import models


class CheckpointLink(models.Model):
    # Link to a checkpoint for a given "thread+agent"
    # The langgraph's checkpoint is identified by checkpoint_id
    thread = models.ForeignKey('Thread', on_delete=models.CASCADE,
                               related_name='checkpoint_links')
    agent = models.ForeignKey('AgentConfig', on_delete=models.CASCADE)
    checkpoint_id = models.UUIDField(primary_key=True,
                                     default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('thread', 'agent'),)

    def __str__(self):
        return f"Link to Checkpoint {self.checkpoint_id} for Thread {self.thread.id} and agent {self.agent.id}"
