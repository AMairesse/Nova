# nova/models/WebApp.py
import posixpath

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
    User-owned, thread-scoped static mini-application served live from a source directory.
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
    name = models.CharField(max_length=120, blank=True, default="")
    slug = models.SlugField(default=uuid_hex, unique=True)
    source_root = models.CharField(max_length=255, blank=True, default="")
    entry_path = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Web application")
        verbose_name_plural = _("Web applications")
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['user']),
            models.Index(fields=['thread']),
            models.Index(fields=['thread', 'source_root'], name='nova_webapp_thread_source_idx'),
        ]

    def clean(self):
        # Enforce multi-tenancy consistency: the WebApp user must match the thread owner
        if self.thread_id and self.user_id and self.thread.user_id != self.user_id:
            raise ValidationError(_("WebApp user must match Thread owner."))
        if self.source_root:
            normalized_root = posixpath.normpath(str(self.source_root or "").strip() or "/")
            if not normalized_root.startswith("/"):
                raise ValidationError(_("WebApp source_root must be an absolute path."))
            self.source_root = normalized_root
        if self.entry_path:
            normalized_entry = posixpath.normpath(str(self.entry_path or "").strip())
            if normalized_entry in {"", ".", "/", ".."} or normalized_entry.startswith("../"):
                raise ValidationError(_("WebApp entry_path must stay inside source_root."))
            if normalized_entry.startswith("/"):
                raise ValidationError(_("WebApp entry_path must be relative to source_root."))
            self.entry_path = normalized_entry

    def __str__(self) -> str:
        display_name = (self.name or "").strip()
        return display_name or self.slug
