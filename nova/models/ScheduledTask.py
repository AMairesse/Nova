# nova/models/ScheduledTask.py
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
import croniter
from cron_descriptor import get_description
from django_celery_beat.models import PeriodicTask, CrontabSchedule


class ScheduledTask(models.Model):
    class TaskKind(models.TextChoices):
        AGENT = "agent", _("Agent")
        MAINTENANCE = "maintenance", _("Maintenance")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scheduled_tasks',
        verbose_name=_("User")
    )
    agent = models.ForeignKey(
        'AgentConfig',
        on_delete=models.CASCADE,
        related_name='scheduled_tasks',
        verbose_name=_("Agent"),
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120, verbose_name=_("Task name"))
    prompt = models.TextField(verbose_name=_("Prompt"), blank=True, default="")
    cron_expression = models.CharField(max_length=100, verbose_name=_("Cron expression"))
    timezone = models.CharField(max_length=50, default='UTC', verbose_name=_("Timezone"))
    keep_thread = models.BooleanField(default=True, verbose_name=_("Keep thread after execution"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))
    task_kind = models.CharField(
        max_length=32,
        choices=TaskKind.choices,
        default=TaskKind.AGENT,
        verbose_name=_("Task kind"),
    )
    # Used when task_kind == MAINTENANCE
    maintenance_task = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Maintenance task"),
        help_text=_("Celery task name, e.g. 'continuous_nightly_daysegment_summaries_for_user'."),
    )
    last_error = models.TextField(blank=True, null=True, verbose_name=_("Last error"))
    last_run_at = models.DateTimeField(blank=True, null=True, verbose_name=_("Last run at"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "name"),)
        verbose_name = _("Scheduled task")
        verbose_name_plural = _("Scheduled tasks")

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    def get_schedule_description(self):
        """Return a human-readable description of the cron schedule."""
        try:
            return get_description(self.cron_expression)
        except Exception:
            return _("Invalid cron expression")

    def clean(self):
        super().clean()
        # Validate cron expression
        try:
            croniter.croniter(self.cron_expression)
        except Exception as e:
            raise ValidationError(_("Invalid cron expression: %(error)s") % {'error': str(e)})

        # Kind-specific validation
        if self.task_kind == self.TaskKind.AGENT:
            if not self.agent_id:
                raise ValidationError(_("Agent is required for an agent scheduled task."))
            if not (self.prompt or "").strip():
                raise ValidationError(_("Prompt is required for an agent scheduled task."))
        elif self.task_kind == self.TaskKind.MAINTENANCE:
            if not (self.maintenance_task or "").strip():
                raise ValidationError(_("Maintenance task is required for a maintenance scheduled task."))

            # Maintenance tasks must remain daily. Users may change only time (minute/hour)
            # and timezone.
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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # Create/update Celery Beat PeriodicTask
        if self.is_active:
            # Parse cron expression
            # croniter expects: minute hour day month day_of_week
            cron_parts = self.cron_expression.split()
            if len(cron_parts) != 5:
                raise ValidationError("Invalid cron expression format")

            minute, hour, day_of_month, month_of_year, day_of_week = cron_parts

            # Create or get CrontabSchedule
            crontab, created = CrontabSchedule.objects.get_or_create(
                minute=minute,
                hour=hour,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                day_of_week=day_of_week,
                timezone=self.timezone
            )

            # Create or update PeriodicTask
            task_name = f"scheduled_task_{self.id}"
            periodic_task, created = PeriodicTask.objects.get_or_create(
                name=task_name,
                defaults={
                    'task': 'run_scheduled_agent_task'
                    if self.task_kind == self.TaskKind.AGENT
                    else self.maintenance_task,
                    'crontab': crontab,
                    # Agent tasks keep legacy args=[scheduled_task_id].
                    # Maintenance tasks use kwargs={'user_id': ...}.
                    'args': f'[{self.id}]' if self.task_kind == self.TaskKind.AGENT else '[]',
                    'kwargs': (
                        '{}'
                        if self.task_kind == self.TaskKind.AGENT
                        else f'{{"user_id": {self.user_id}}}'
                    ),
                    'enabled': True,
                }
            )

            if not created:
                # Update existing task
                periodic_task.crontab = crontab
                periodic_task.task = (
                    'run_scheduled_agent_task'
                    if self.task_kind == self.TaskKind.AGENT
                    else self.maintenance_task
                )
                periodic_task.args = f'[{self.id}]' if self.task_kind == self.TaskKind.AGENT else '[]'
                if self.task_kind == self.TaskKind.AGENT:
                    periodic_task.kwargs = '{}'
                else:
                    periodic_task.kwargs = f'{{"user_id": {self.user_id}}}'
                periodic_task.enabled = True
                periodic_task.save()
        else:
            # Disable the task if it exists
            try:
                periodic_task = PeriodicTask.objects.get(name=f"scheduled_task_{self.id}")
                periodic_task.enabled = False
                periodic_task.save()
            except PeriodicTask.DoesNotExist:
                pass

    def delete(self, *args, **kwargs):
        # Delete associated PeriodicTask
        try:
            periodic_task = PeriodicTask.objects.get(name=f"scheduled_task_{self.id}")
            periodic_task.delete()
        except PeriodicTask.DoesNotExist:
            pass
        super().delete(*args, **kwargs)
