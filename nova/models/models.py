# nova/models/models.py
import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


# ----- Task Model for Asynchronous AI Tasks -----
class TaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    AWAITING_INPUT = "AWAITING_INPUT", _("Awaiting user input")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")


class Task(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='tasks',
                             verbose_name=_("User tasks"))
    thread = models.ForeignKey('Thread',
                               on_delete=models.CASCADE,
                               related_name='tasks',
                               verbose_name=_("Thread"))
    agent = models.ForeignKey('AgentConfig',
                              on_delete=models.SET_NULL,
                              null=True,
                              blank=True,
                              related_name='tasks',
                              verbose_name=_("AgentConfig"))
    status = models.CharField(max_length=20,
                              choices=TaskStatus.choices,
                              default=TaskStatus.PENDING)
    # List of dicts, e.g., [{"step": "Calling tool X", "timestamp": "2025-07-28T03:58:00Z"}]
    progress_logs = models.JSONField(default=list, blank=True)
    # Final output or error message
    result = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task {self.id} for Thread {self.thread.subject} ({self.status})"


class InteractionStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ANSWERED = "ANSWERED", _("Answered")
    CANCELED = "CANCELED", _("Canceled")


class Interaction(models.Model):
    """
    Represents a blocking question asked to the end-user during an agent run.
    Exactly one pending interaction per Task at a given time.
    """
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='interactions',
        verbose_name=_("Task")
    )
    thread = models.ForeignKey(
        'Thread',
        on_delete=models.CASCADE,
        related_name='interactions',
        verbose_name=_("Thread")
    )
    # Optional: the agent (or sub-agent) that asked the question
    agent = models.ForeignKey(
        'AgentConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interactions',
        verbose_name=_("AgentConfig")
    )
    # Free-text origin for UI (e.g., "Calendar Agent", "Main Agent")
    origin_name = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name=_("Origin (display name)")
    )

    question = models.TextField(verbose_name=_("Question to user"))
    answer = models.JSONField(blank=True, null=True, default=None, verbose_name=_("User answer"))

    # Optional JSON schema describing expected answer shape
    schema = models.JSONField(default=dict, blank=True, null=True)

    # Payload to store engine-specific resume token/metadata (interrupt handle)
    resume_payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=10,
        choices=InteractionStatus.choices,
        default=InteractionStatus.PENDING
    )

    # Optional expiration / auto-cancel policy (handled at app level later)
    expires_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['task', 'status']),
            models.Index(fields=['thread', 'status']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = _("Interaction")
        verbose_name_plural = _("Interactions")

    def __str__(self):
        origin = self.origin_name or (self.agent.name if self.agent else "Agent")
        return f"Interaction[{self.id}] {origin}: {self.question[:40]}..."

    def clean(self):
        super().clean()
        # Ensure the interaction's thread matches the task thread
        if self.thread_id and self.task_id and self.thread_id != self.task.thread_id:
            raise ValidationError(_("Interaction thread must match task thread."))

        # Enforce single PENDING interaction per task (app-level validation)
        if self.status == InteractionStatus.PENDING and self.task_id:
            qs = Interaction.objects.filter(task_id=self.task_id, status=InteractionStatus.PENDING)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(_("There is already a pending interaction for this task."))


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
