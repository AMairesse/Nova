# nova/continuous/utils.py

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from nova.models.Thread import Thread


User = get_user_model()


@dataclass(frozen=True)
class ContinuousContext:
    thread: Thread
    day_label: dt.date


def _get_user_tz(user) -> dt.tzinfo:
    """Return the user's timezone.

    V1: fallback to Django's current timezone.
    Later: read from user preferences if present.
    """
    # TODO: integrate a real user preference once available
    return timezone.get_current_timezone()


def get_day_label_for_user(user, when: dt.datetime | None = None) -> dt.date:
    tz = _get_user_tz(user)
    when = when or timezone.now()
    local = timezone.localtime(when, tz)
    return local.date()


def ensure_continuous_thread(user) -> Thread:
    """Get or create the unique continuous thread for a user.

    This must be idempotent under concurrent requests.
    """
    with transaction.atomic():
        thread = Thread.objects.filter(user=user, mode=Thread.Mode.CONTINUOUS).first()
        if thread:
            return thread

        # Create with a stable subject; we'll keep it simple for V1.
        try:
            return Thread.objects.create(
                user=user,
                subject="Continuous",
                mode=Thread.Mode.CONTINUOUS,
            )
        except IntegrityError:
            # Another request created it.
            return Thread.objects.get(user=user, mode=Thread.Mode.CONTINUOUS)


def ensure_day_segment(user, thread: Thread, day_label: dt.date):
    """Return the DaySegment for (user, thread, day_label) if it exists."""
    from nova.models.DaySegment import DaySegment

    return DaySegment.objects.filter(user=user, thread=thread, day_label=day_label).first()


def get_or_create_day_segment(user, thread: Thread, day_label: dt.date, *, starts_at_message):
    """Idempotently create the DaySegment for (user, thread, day_label).

    The DaySegment is opened on the first message of the day (user timezone).
    """
    from nova.models.DaySegment import DaySegment

    with transaction.atomic():
        segment = DaySegment.objects.filter(user=user, thread=thread, day_label=day_label).first()
        if segment:
            return segment

        try:
            return DaySegment.objects.create(
                user=user,
                thread=thread,
                day_label=day_label,
                starts_at_message=starts_at_message,
                summary_markdown="",
            )
        except IntegrityError:
            return DaySegment.objects.get(user=user, thread=thread, day_label=day_label)
