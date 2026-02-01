# nova/models/DaySegment.py

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DaySegment(models.Model):
    """A day slice for the user's continuous thread.

    V1: we only store the day label + the first message of the day and an optional
    Markdown summary. The UI may fold messages based on `updated_at`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="day_segments",
        verbose_name=_("User"),
    )
    thread = models.ForeignKey(
        "Thread",
        on_delete=models.CASCADE,
        related_name="day_segments",
        verbose_name=_("Thread"),
    )
    day_label = models.DateField(db_index=True)

    starts_at_message = models.ForeignKey(
        "Message",
        on_delete=models.PROTECT,
        related_name="day_segment_starts",
        verbose_name=_("Starts at message"),
    )

    summary_markdown = models.TextField(blank=True, default="")

    # If a summary is generated, it should conceptually summarize messages from
    # `starts_at_message` up to this message (inclusive). When rebuilding the
    # checkpoint in continuous mode, we include only messages AFTER this pointer.
    summary_until_message = models.ForeignKey(
        "Message",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="day_segment_summary_until",
        verbose_name=_("Summary until message"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "thread", "day_label"],
                name="uniq_daysegment_user_thread_day",
            )
        ]
        indexes = [
            models.Index(fields=["user", "thread", "day_label"]),
        ]

    def __str__(self) -> str:
        return f"DaySegment({self.user_id}, {self.thread_id}, {self.day_label})"
