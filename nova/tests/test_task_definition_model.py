from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from django_celery_beat.models import PeriodicTask

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool


User = get_user_model()


class TaskDefinitionModelBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="taskdef-user", password="x")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="provider",
            provider_type=ProviderType.OLLAMA,
            model="llama3.2",
            base_url="http://localhost:11434",
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="agent",
            llm_provider=self.provider,
            system_prompt="You are helpful.",
        )
        self.email_tool = Tool.objects.create(
            user=self.user,
            name="Email",
            description="Email tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        self.non_email_tool = Tool.objects.create(
            user=self.user,
            name="Searx",
            description="Search tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.tools.builtins.searxng",
        )

    def _build_agent_cron_task(self, name="agent-cron"):
        return TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.CRON,
            agent=self.agent,
            prompt="Run a quick check.",
            run_mode=TaskDefinition.RunMode.NEW_THREAD,
            cron_expression="0 6 * * *",
            timezone="UTC",
            is_active=True,
        )

    def _create_agent_cron_task(self, name="agent-cron"):
        task_def = self._build_agent_cron_task(name=name)
        task_def.full_clean()
        task_def.save()
        return task_def

    def _create_agent_email_task(self, name="agent-email", poll_interval=5):
        task_def = TaskDefinition(
            user=self.user,
            name=name,
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
            agent=self.agent,
            prompt="Handle new emails.",
            run_mode=TaskDefinition.RunMode.CONTINUOUS_MESSAGE,
            email_tool=self.email_tool,
            poll_interval_minutes=poll_interval,
            timezone="UTC",
            is_active=True,
        )
        task_def.full_clean()
        task_def.save()
        return task_def


class TaskDefinitionValidationTests(TaskDefinitionModelBase):
    def test_agent_task_requires_agent(self):
        task_def = self._build_agent_cron_task(name="missing-agent")
        task_def.agent = None

        with self.assertRaises(ValidationError) as ctx:
            task_def.full_clean()

        self.assertIn("Agent is required for an agent task", str(ctx.exception))

    def test_agent_task_requires_prompt(self):
        task_def = self._build_agent_cron_task(name="missing-prompt")
        task_def.prompt = "   "

        with self.assertRaises(ValidationError) as ctx:
            task_def.full_clean()

        self.assertIn("Prompt is required for an agent task", str(ctx.exception))

    def test_maintenance_task_must_be_daily_cron(self):
        task_def = TaskDefinition(
            user=self.user,
            name="maintenance-weekly",
            task_kind=TaskDefinition.TaskKind.MAINTENANCE,
            trigger_type=TaskDefinition.TriggerType.CRON,
            maintenance_task="continuous_nightly_daysegment_summaries_for_user",
            cron_expression="0 6 * * 1",
            timezone="UTC",
            run_mode=TaskDefinition.RunMode.EPHEMERAL,
            is_active=True,
        )

        with self.assertRaises(ValidationError) as ctx:
            task_def.full_clean()

        self.assertIn("Maintenance tasks must run daily", str(ctx.exception))

    def test_email_poll_requires_email_tool_subtype(self):
        task_def = TaskDefinition(
            user=self.user,
            name="bad-email-tool",
            task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
            agent=self.agent,
            prompt="Check email",
            run_mode=TaskDefinition.RunMode.NEW_THREAD,
            email_tool=self.non_email_tool,
            poll_interval_minutes=5,
            timezone="UTC",
            is_active=True,
        )

        with self.assertRaises(ValidationError) as ctx:
            task_def.full_clean()

        self.assertIn("Selected tool must be the built-in email tool", str(ctx.exception))

    def test_email_poll_interval_must_be_between_1_and_15(self):
        for interval in (0, 16):
            task_def = TaskDefinition(
                user=self.user,
                name=f"email-interval-{interval}",
                task_kind=TaskDefinition.TaskKind.AGENT,
                trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
                agent=self.agent,
                prompt="Check email",
                run_mode=TaskDefinition.RunMode.NEW_THREAD,
                email_tool=self.email_tool,
                poll_interval_minutes=interval,
                timezone="UTC",
                is_active=True,
            )

            with self.assertRaises(ValidationError) as ctx:
                task_def.full_clean()

            self.assertIn("Polling interval must be between 1 and 15 minutes", str(ctx.exception))


class TaskDefinitionBeatSyncTests(TaskDefinitionModelBase):
    def test_cron_task_save_creates_periodic_task_with_crontab(self):
        task_def = self._create_agent_cron_task(name="daily-6utc")

        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        self.assertEqual(periodic.task, "run_task_definition_cron")
        self.assertIsNotNone(periodic.crontab)
        self.assertIsNone(periodic.interval)

    def test_email_poll_save_creates_periodic_task_with_interval(self):
        task_def = self._create_agent_email_task(name="email-poll", poll_interval=3)

        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        self.assertEqual(periodic.task, "poll_task_definition_email")
        self.assertIsNotNone(periodic.interval)
        self.assertIsNone(periodic.crontab)
        self.assertEqual(periodic.interval.every, 3)

    def test_switching_trigger_updates_periodic_task_schedule(self):
        task_def = self._create_agent_cron_task(name="switch-trigger")
        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        periodic_id = periodic.id

        task_def.trigger_type = TaskDefinition.TriggerType.EMAIL_POLL
        task_def.email_tool = self.email_tool
        task_def.poll_interval_minutes = 7
        task_def.full_clean()
        task_def.save()

        periodic.refresh_from_db()
        self.assertEqual(periodic.id, periodic_id)
        self.assertEqual(periodic.task, "poll_task_definition_email")
        self.assertIsNotNone(periodic.interval)
        self.assertIsNone(periodic.crontab)
        self.assertEqual(periodic.interval.every, 7)

    def test_deactivate_task_disables_periodic_task(self):
        task_def = self._create_agent_cron_task(name="deactivate")

        task_def.is_active = False
        task_def.save(update_fields=["is_active", "updated_at"])

        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        self.assertFalse(periodic.enabled)

    def test_runtime_only_save_does_not_resync_periodic_schedule(self):
        task_def = self._create_agent_cron_task(name="runtime-save")
        periodic = PeriodicTask.objects.get(name=f"task_definition_{task_def.id}")
        before_periodic_id = periodic.id
        before_crontab_id = periodic.crontab_id

        task_def.last_error = "temporary error"
        task_def.save(update_fields=["last_error", "updated_at"])

        periodic.refresh_from_db()
        self.assertEqual(periodic.id, before_periodic_id)
        self.assertEqual(periodic.crontab_id, before_crontab_id)
        self.assertTrue(periodic.enabled)

