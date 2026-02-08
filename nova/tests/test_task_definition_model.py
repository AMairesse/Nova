from django.contrib.auth import get_user_model
from django.test import TestCase

from django_celery_beat.models import PeriodicTask

from nova.models.TaskDefinition import TaskDefinition


User = get_user_model()


class TaskDefinitionModelTests(TestCase):
    def test_cron_task_save_creates_periodic_task_with_crontab(self):
        user = User.objects.create_user(username="taskdef-user", password="x")

        task_def = TaskDefinition(
            user=user,
            name="daily-6utc",
            task_kind=TaskDefinition.TaskKind.MAINTENANCE,
            trigger_type=TaskDefinition.TriggerType.CRON,
            maintenance_task="continuous_nightly_daysegment_summaries_for_user",
            cron_expression="0 6 * * *",
            timezone="UTC",
            run_mode=TaskDefinition.RunMode.EPHEMERAL,
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()

        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        self.assertIsNotNone(periodic.crontab)
        self.assertIsNone(periodic.interval)
