# nova/models/WebApp.py
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from uuid import uuid4

from nova.models.Thread import Thread


def uuid_hex():
    return uuid4().hex


class WebApp(models.Model):
    """
    User-owned, thread-scoped static mini-application (HTML/CSS/JS files only).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='webapps',
        verbose_name=_("User webapps"),
    )
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name='webapps',
        verbose_name=_("Thread"),
    )
    slug = models.SlugField(default=uuid_hex, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Web application")
        verbose_name_plural = _("Web applications")
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['user']),
            models.Index(fields=['thread']),
        ]

    def clean(self):
        # Enforce multi-tenancy consistency: the WebApp user must match the thread owner
        if self.thread_id and self.user_id and self.thread.user_id != self.user_id:
            raise ValidationError(_("WebApp user must match Thread owner."))

    def __str__(self) -> str:
        return self.slug
