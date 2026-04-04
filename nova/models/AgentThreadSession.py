from django.db import models


class AgentThreadSession(models.Model):
    thread = models.ForeignKey(
        "Thread",
        on_delete=models.CASCADE,
        related_name="agent_sessions",
    )
    agent_config = models.ForeignKey(
        "AgentConfig",
        on_delete=models.CASCADE,
        related_name="thread_sessions",
    )
    runtime_engine = models.CharField(max_length=32, default="react_terminal_v1", db_index=True)
    session_state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("thread", "agent_config", "runtime_engine"),)

    def __str__(self):
        return f"Session(thread={self.thread_id}, agent={self.agent_config_id}, runtime={self.runtime_engine})"
