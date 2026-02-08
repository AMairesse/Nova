from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from pgvector.django import VectorField


class ConversationEmbeddingState(models.TextChoices):
    PENDING = "pending", _("pending")
    READY = "ready", _("ready")
    ERROR = "error", _("error")


class DaySegmentEmbedding(models.Model):
    """Embedding vector for a DaySegment summary."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="day_segment_embeddings",
    )
    day_segment = models.OneToOneField(
        "DaySegment",
        on_delete=models.CASCADE,
        related_name="embedding",
    )

    provider_type = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    dimensions = models.IntegerField(null=True, blank=True)

    state = models.CharField(
        max_length=20,
        choices=ConversationEmbeddingState.choices,
        default=ConversationEmbeddingState.PENDING,
    )
    error = models.TextField(null=True, blank=True)

    vector = VectorField(dimensions=1024, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "state"], name="idx_dayseg_embed_u_state"),
        ]


class TranscriptChunkEmbedding(models.Model):
    """Embedding vector for a TranscriptChunk content."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transcript_chunk_embeddings",
    )
    transcript_chunk = models.OneToOneField(
        "TranscriptChunk",
        on_delete=models.CASCADE,
        related_name="embedding",
    )

    provider_type = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    dimensions = models.IntegerField(null=True, blank=True)

    state = models.CharField(
        max_length=20,
        choices=ConversationEmbeddingState.choices,
        default=ConversationEmbeddingState.PENDING,
    )
    error = models.TextField(null=True, blank=True)

    vector = VectorField(dimensions=1024, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "state"], name="idx_chunk_embed_u_state"),
        ]
