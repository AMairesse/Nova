from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

import croniter
from cron_descriptor import get_description
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


class TaskDefinition(models.Model):
    class TaskKind(models.TextChoices):
        AGENT = "agent", _("Agent")
        MAINTENANCE = "maintenance", _("Maintenance")

    class TriggerType(models.TextChoices):
        CRON = "cron", _("Schedule (cron)")
        EMAIL_POLL = "email_poll", _("Email polling")

    class RunMode(models.TextChoices):
        NEW_THREAD = "new_thread", _("New thread")
        CONTINUOUS_MESSAGE = "continuous_message", _("Message in Continuous")
        EPHEMERAL = "ephemeral", _("Ephemeral (no persisted thread)")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_definitions",
        verbose_name=_("User"),
    )
    name = models.CharField(max_length=120, verbose_name=_("Task name"))
    task_kind = models.CharField(
        max_length=32,
        choices=TaskKind.choices,
        default=TaskKind.AGENT,
        verbose_name=_("Task kind"),
    )
    trigger_type = models.CharField(
        max_length=32,
        choices=TriggerType.choices,
        default=TriggerType.CRON,
        verbose_name=_("Trigger type"),
    )

    # Agent execution config (used for task_kind == AGENT)
    agent = models.ForeignKey(
        "AgentConfig",
        on_delete=models.CASCADE,
        related_name="task_definitions",
        verbose_name=_("Agent"),
        null=True,
        blank=True,
    )
    prompt = models.TextField(verbose_name=_("Prompt"), blank=True, default="")
    run_mode = models.CharField(
        max_length=32,
        choices=RunMode.choices,
        default=RunMode.NEW_THREAD,
        verbose_name=_("Run mode"),
    )

    # Trigger config (cron)
    cron_expression = models.CharField(max_length=100, verbose_name=_("Cron expression"), blank=True, default="")
    timezone = models.CharField(max_length=50, default="UTC", verbose_name=_("Timezone"))

    # Trigger config (email polling)
    email_tool = models.ForeignKey(
        "Tool",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_definitions",
        verbose_name=_("Email tool"),
    )
    poll_interval_minutes = models.PositiveSmallIntegerField(
        default=5,
        verbose_name=_("Polling interval (minutes)"),
    )

    # Maintenance config (used for task_kind == MAINTENANCE)
    maintenance_task = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Maintenance task"),
        help_text=_("Celery task name, e.g. 'continuous_nightly_daysegment_summaries_for_user'."),
    )

    # Internal cursor/state for trigger runtime (email UID cursor, last poll, ...)
    runtime_state = models.JSONField(default=dict, blank=True, verbose_name=_("Runtime state"))

    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))
    last_error = models.TextField(blank=True, null=True, verbose_name=_("Last error"))
    last_run_at = models.DateTimeField(blank=True, null=True, verbose_name=_("Last run at"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "name"),)
        verbose_name = _("Task definition")
        verbose_name_plural = _("Task definitions")

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    def get_schedule_description(self):
        """Return a human-readable description of the trigger schedule."""
        if self.trigger_type == self.TriggerType.EMAIL_POLL:
            return _("Every %(n)s minute(s), poll unseen emails") % {"n": self.poll_interval_minutes}
        try:
            return get_description(self.cron_expression)
        except Exception:
            return _("Invalid cron expression")

    def _periodic_task_name(self) -> str:
        return f"task_definition_{self.id}"

    def _celery_task_name(self) -> str:
        if self.task_kind == self.TaskKind.MAINTENANCE:
            return "run_task_definition_maintenance"
        if self.trigger_type == self.TriggerType.EMAIL_POLL:
            return "poll_task_definition_email"
        return "run_task_definition_cron"

    def clean(self):
        super().clean()

        # Validate kind-specific requirements.
        if self.task_kind == self.TaskKind.AGENT:
            if not self.agent_id:
                raise ValidationError(_("Agent is required for an agent task."))
            if not (self.prompt or "").strip():
                raise ValidationError(_("Prompt is required for an agent task."))
        elif self.task_kind == self.TaskKind.MAINTENANCE:
            if not (self.maintenance_task or "").strip():
                raise ValidationError(_("Maintenance task is required for a maintenance task."))
            # Maintenance tasks are scheduler-based (not email-triggered).
            if self.trigger_type != self.TriggerType.CRON:
                raise ValidationError(_("Maintenance tasks must use a cron trigger."))

        # Validate trigger-specific requirements.
        if self.trigger_type == self.TriggerType.CRON:
            if not (self.cron_expression or "").strip():
                raise ValidationError(_("Cron expression is required for cron-triggered tasks."))
            try:
                croniter.croniter(self.cron_expression)
            except Exception as e:
                raise ValidationError(_("Invalid cron expression: %(error)s") % {"error": str(e)})

            # Maintenance tasks remain daily with editable hour/minute/timezone.
            if self.task_kind == self.TaskKind.MAINTENANCE:
                cron_parts = (self.cron_expression or "").split()
                if len(cron_parts) != 5:
                    raise ValidationError(_("Cron expression must have 5 parts: minute hour day month weekday."))
                _minute, _hour, day_of_month, month_of_year, day_of_week = cron_parts
                if not (day_of_month == month_of_year == day_of_week == "*"):
                    raise ValidationError(
                        _(
                            "Maintenance tasks must run daily. Only minute/hour and timezone can be changed "
                            "(expected cron like 'm H * * *')."
                        )
                    )
        elif self.trigger_type == self.TriggerType.EMAIL_POLL:
            if self.task_kind != self.TaskKind.AGENT:
                raise ValidationError(_("Email polling trigger is available only for agent tasks."))
            if not self.email_tool_id:
                raise ValidationError(_("Email tool is required for email polling trigger."))
            if not (1 <= int(self.poll_interval_minutes or 0) <= 15):
                raise ValidationError(_("Polling interval must be between 1 and 15 minutes."))

            # We only support the built-in email tool in V1.
            if self.email_tool and self.email_tool.tool_subtype != "email":
                raise ValidationError(_("Selected tool must be the built-in email tool."))

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        super().save(*args, **kwargs)

        # Runtime-only updates (last_run_at, last_error, runtime_state...) must not
        # resync Beat schedules on each poll/execution.
        schedule_fields = {
            "is_active",
            "task_kind",
            "trigger_type",
            "cron_expression",
            "timezone",
            "poll_interval_minutes",
            "maintenance_task",
        }
        if update_fields is not None and not (set(update_fields) & schedule_fields):
            return

        if self.is_active:
            periodic_task, _created = PeriodicTask.objects.get_or_create(
                name=self._periodic_task_name(),
                defaults={
                    "task": self._celery_task_name(),
                    "args": f"[{self.id}]",
                    "kwargs": "{}",
                    "enabled": True,
                },
            )

            periodic_task.task = self._celery_task_name()
            periodic_task.args = f"[{self.id}]"
            periodic_task.kwargs = "{}"
            periodic_task.enabled = True

            if self.trigger_type == self.TriggerType.CRON:
                cron_parts = self.cron_expression.split()
                if len(cron_parts) != 5:
                    raise ValidationError("Invalid cron expression format")
                minute, hour, day_of_month, month_of_year, day_of_week = cron_parts

                crontab, _ = CrontabSchedule.objects.get_or_create(
                    minute=minute,
                    hour=hour,
                    day_of_month=day_of_month,
                    month_of_year=month_of_year,
                    day_of_week=day_of_week,
                    timezone=self.timezone,
                )
                periodic_task.crontab = crontab
                periodic_task.interval = None
                periodic_task.solar = None
                periodic_task.clocked = None
            else:
                interval, _ = IntervalSchedule.objects.get_or_create(
                    every=self.poll_interval_minutes,
                    period=IntervalSchedule.MINUTES,
                )
                periodic_task.interval = interval
                periodic_task.crontab = None
                periodic_task.solar = None
                periodic_task.clocked = None

            # Do not keep running stale retries if a task is disabled/reconfigured.
            periodic_task.one_off = False
            periodic_task.save()
        else:
            try:
                periodic_task = PeriodicTask.objects.get(name=self._periodic_task_name())
                periodic_task.enabled = False
                periodic_task.save()
            except PeriodicTask.DoesNotExist:
                pass

    def delete(self, *args, **kwargs):
        try:
            periodic_task = PeriodicTask.objects.get(name=self._periodic_task_name())
            periodic_task.delete()
        except PeriodicTask.DoesNotExist:
            pass
        super().delete(*args, **kwargs)
