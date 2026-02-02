# nova/models/TranscriptChunk.py

import hashlib

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TranscriptChunk(models.Model):
    """Search/index unit for the continuous discussion transcript.

    V1: append-only chunking. Embeddings are out of scope.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transcript_chunks",
        verbose_name=_("User"),
    )
    thread = models.ForeignKey(
        "Thread",
        on_delete=models.CASCADE,
        related_name="transcript_chunks",
        verbose_name=_("Thread"),
    )
    day_segment = models.ForeignKey(
        "DaySegment",
        on_delete=models.CASCADE,
        related_name="transcript_chunks",
        null=True,
        blank=True,
        verbose_name=_("Day segment"),
    )

    start_message = models.ForeignKey(
        "Message",
        on_delete=models.PROTECT,
        related_name="transcript_chunk_starts",
        verbose_name=_("Start message"),
    )
    end_message = models.ForeignKey(
        "Message",
        on_delete=models.PROTECT,
        related_name="transcript_chunk_ends",
        verbose_name=_("End message"),
    )

    content_text = models.TextField(help_text=_("Normalized concatenation of message contents"))
    content_hash = models.CharField(max_length=64, db_index=True)
    token_estimate = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "thread", "start_message", "end_message"],
                name="uniq_chunk_user_thread_bounds",
            )
        ]
        indexes = [
            models.Index(fields=["user", "thread", "day_segment", "start_message"], name="idx_chunk_day_start"),
            models.Index(fields=["user", "thread", "end_message"], name="idx_chunk_end"),
        ]

    def __str__(self) -> str:
        return f"TranscriptChunk({self.thread_id}, {self.start_message_id}-{self.end_message_id})"

    @staticmethod
    def compute_hash(content_text: str, start_message_id: int, end_message_id: int) -> str:
        h = hashlib.sha256()
        h.update(str(start_message_id).encode("utf-8"))
        h.update(b":")
        h.update(str(end_message_id).encode("utf-8"))
        h.update(b":")
        h.update((content_text or "").encode("utf-8"))
        return h.hexdigest()
