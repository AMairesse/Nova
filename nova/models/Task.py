# nova/models/models.py
import logging

from django.conf import settings
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
