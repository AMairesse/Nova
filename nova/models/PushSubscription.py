# nova/models/PushSubscription.py
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class PushSubscription(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
        verbose_name=_("User push subscriptions"),
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    expiration_time = models.DateTimeField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_error = models.TextField(blank=True, default="")
    last_success_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        return f"PushSubscription(user={self.user_id}, active={self.is_active})"
