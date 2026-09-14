from django.conf import settings
from django.db import models


class RoutineRun(models.Model):
    """Occurrence receipt, retained even when its ephemeral conversation is removed."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    definition = models.ForeignKey('TaskDefinition', on_delete=models.SET_NULL, null=True, related_name='runs')
    name = models.CharField(max_length=120)
    key = models.CharField(max_length=255, unique=True)
    task = models.OneToOneField('Task', null=True, blank=True, on_delete=models.SET_NULL, related_name='routine_run')
    status = models.CharField(max_length=32, default='PENDING')
    reason = models.TextField(blank=True, default='')
    result = models.TextField(blank=True, default='')
    cursor_state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['user', '-created_at'], name='routine_user_created')]
