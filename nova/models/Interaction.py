# nova/models/Interaction.py
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


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
        'Task',
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
    agent_config = models.ForeignKey(
        'AgentConfig',
        on_delete=models.CASCADE,
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
        return f"Interaction[{self.id}] {self.origin_name}: {self.question[:40]}..."

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
