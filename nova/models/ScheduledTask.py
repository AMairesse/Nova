# nova/models/ScheduledTask.py
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
import croniter
from django_celery_beat.models import PeriodicTask, CrontabSchedule


class ScheduledTask(models.Model):
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
        verbose_name=_("Agent")
    )
    name = models.CharField(max_length=120, verbose_name=_("Task name"))
    prompt = models.TextField(verbose_name=_("Prompt"))
    cron_expression = models.CharField(max_length=100, verbose_name=_("Cron expression"))
    timezone = models.CharField(max_length=50, default='UTC', verbose_name=_("Timezone"))
    keep_thread = models.BooleanField(default=True, verbose_name=_("Keep thread after execution"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))
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

    def clean(self):
        super().clean()
        # Validate cron expression
        try:
            croniter.croniter(self.cron_expression)
        except Exception as e:
            raise ValidationError(_("Invalid cron expression: %(error)s") % {'error': str(e)})

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
                    'task': 'nova.tasks.scheduled_tasks.run_scheduled_agent_task',
                    'crontab': crontab,
                    'args': f'[{self.id}]',
                    'enabled': True,
                }
            )

            if not created:
                # Update existing task
                periodic_task.crontab = crontab
                periodic_task.args = f'[{self.id}]'
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
