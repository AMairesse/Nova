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
            # Best-effort: ensure the per-user nightly maintenance task exists.
            # This keeps the behavior user-visible/editable via Tasks.
            try:
                ensure_continuous_nightly_summary_task_definition(user)
            except Exception:
                pass
            return thread

        # Create with a stable subject; we'll keep it simple for V1.
        try:
            thread = Thread.objects.create(
                user=user,
                subject="Continuous",
                mode=Thread.Mode.CONTINUOUS,
            )
            # Best-effort: schedule nightly summaries for this user.
            try:
                ensure_continuous_nightly_summary_task_definition(user)
            except Exception:
                pass
            return thread
        except IntegrityError:
            # Another request created it.
            thread = Thread.objects.get(user=user, mode=Thread.Mode.CONTINUOUS)
            try:
                ensure_continuous_nightly_summary_task_definition(user)
            except Exception:
                pass
            return thread


def ensure_continuous_nightly_summary_task_definition(user) -> None:
    """Ensure the per-user nightly summary maintenance task exists.

    This is implemented as a user-owned TaskDefinition so it appears in the UI
    (Tasks) and the user can modify the schedule.
    """

    from nova.models.TaskDefinition import TaskDefinition

    # Default: 02:00 UTC daily.
    name = "Continuous: nightly day summaries"
    TaskDefinition.objects.get_or_create(
        user=user,
        name=name,
        defaults={
            "task_kind": TaskDefinition.TaskKind.MAINTENANCE,
            "trigger_type": TaskDefinition.TriggerType.CRON,
            "maintenance_task": "continuous_nightly_daysegment_summaries_for_user",
            "cron_expression": "0 2 * * *",
            "timezone": "UTC",
            "run_mode": TaskDefinition.RunMode.EPHEMERAL,
            "is_active": True,
            # Kept for backward compatibility with existing form/UI until Tasks UI lands.
            "prompt": "",
            "agent": None,
        },
    )


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
