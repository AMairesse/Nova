from django.conf import settings
from django.db import models


class ActionReceipt(models.Model):
    """Minimal receipt: returned successfully is not a claim of remote transaction atomicity."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    task = models.ForeignKey('Task', null=True, on_delete=models.SET_NULL, related_name='action_receipts')
    run = models.ForeignKey('RoutineRun', null=True, on_delete=models.SET_NULL, related_name='action_receipts')
    operation = models.CharField(max_length=80)
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=24, default='STARTED')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
