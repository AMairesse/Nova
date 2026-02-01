# nova/models/CheckpointLink.py
import uuid

from django.db import models
from django.utils import timezone


class CheckpointLink(models.Model):
    # Link to a checkpoint for a given "thread+agent"
    # The langgraph's checkpoint is identified by checkpoint_id
    thread = models.ForeignKey('Thread', on_delete=models.CASCADE,
                               related_name='checkpoint_links')
    agent = models.ForeignKey('AgentConfig', on_delete=models.CASCADE)
    checkpoint_id = models.UUIDField(primary_key=True,
                                     default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Continuous mode only:
    # Used to lazily decide whether we must rebuild the LangGraph checkpoint state
    # (yesterday summary + today summary + today window) before running the agent.
    continuous_context_fingerprint = models.CharField(max_length=64, blank=True, default="")
    continuous_context_built_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = (('thread', 'agent'),)

    def mark_continuous_context_built(self, fingerprint: str) -> None:
        self.continuous_context_fingerprint = fingerprint or ""
        self.continuous_context_built_at = timezone.now()

    def __str__(self):
        return f"Link to Checkpoint {self.checkpoint_id} for Thread {self.thread.id} and agent {self.agent.id}"
