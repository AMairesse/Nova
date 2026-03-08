# nova/models/MessageArtifact.py
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ArtifactDirection(models.TextChoices):
    INPUT = "input", _("Input")
    OUTPUT = "output", _("Output")
    DERIVED = "derived", _("Derived")


class ArtifactKind(models.TextChoices):
    IMAGE = "image", _("Image")
    PDF = "pdf", _("PDF")
    AUDIO = "audio", _("Audio")
    TEXT = "text", _("Text")
    ANNOTATION = "annotation", _("Annotation")


class MessageArtifact(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_artifacts",
    )
    thread = models.ForeignKey(
        "Thread",
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    message = models.ForeignKey(
        "Message",
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    user_file = models.ForeignKey(
        "UserFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="artifacts",
    )
    source_artifact = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_artifacts",
    )
    direction = models.CharField(
        max_length=16,
        choices=ArtifactDirection.choices,
        default=ArtifactDirection.INPUT,
        db_index=True,
    )
    kind = models.CharField(
        max_length=16,
        choices=ArtifactKind.choices,
        db_index=True,
    )
    mime_type = models.CharField(max_length=100, blank=True, default="")
    label = models.CharField(max_length=255, blank=True, default="")
    summary_text = models.TextField(blank=True, default="")
    search_text = models.TextField(blank=True, default="")
    provider_type = models.CharField(max_length=32, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    provider_fingerprint = models.CharField(max_length=64, blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    published_to_file = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["thread", "direction", "kind", "created_at"]),
            models.Index(fields=["message", "direction", "order", "id"]),
        ]

    def save(self, *args, **kwargs):
        if not self.thread_id and self.message_id:
            self.thread = self.message.thread
        if not self.user_id and self.message_id:
            self.user = self.message.user
        if not self.label and self.user_file_id:
            self.label = self.user_file.original_filename.rsplit("/", 1)[-1]
        if not self.mime_type and self.user_file_id:
            self.mime_type = self.user_file.mime_type or ""
        super().save(*args, **kwargs)

    @property
    def filename(self) -> str:
        if self.label:
            return self.label
        if self.user_file_id:
            return self.user_file.original_filename.rsplit("/", 1)[-1]
        return f"{self.kind}-{self.pk or 'artifact'}"

    def to_manifest(self) -> dict:
        size = 0
        if self.user_file_id:
            size = int(getattr(self.user_file, "size", 0) or 0)

        return {
            "id": self.id,
            "message_id": self.message_id,
            "user_file_id": self.user_file_id,
            "direction": self.direction,
            "kind": self.kind,
            "mime_type": self.mime_type or "",
            "label": self.filename,
            "summary_text": self.summary_text or "",
            "size": size,
            "published_to_file": bool(self.published_to_file),
            "metadata": self.metadata or {},
        }

    def __str__(self):
        return f"{self.get_kind_display()} {self.filename}"
