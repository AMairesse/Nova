from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AgentAutonomyInstructionsMigrationTests(TransactionTestCase):
    migrate_from = ("nova", "0086_routine_result_agent_autonomy")
    migrate_to = ("nova", "0087_merge_agent_autonomy_instructions")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        Provider = old_apps.get_model("nova", "LLMProvider")
        AgentConfig = old_apps.get_model("nova", "AgentConfig")

        user = User.objects.create(username="autonomy-migration")
        provider = Provider.objects.create(
            user=user,
            name="Migration Provider",
            model="migration-model",
        )
        self.agent_with_instructions = AgentConfig.objects.create(
            user=user,
            name="With instructions",
            llm_provider=provider,
            system_prompt="  Existing prompt  ",
            autonomy_instructions="  Decide alone.  ",
        )
        self.agent_with_empty_instructions = AgentConfig.objects.create(
            user=user,
            name="With empty instructions",
            llm_provider=provider,
            system_prompt="Keep this prompt",
            autonomy_instructions="",
        )
        self.agent_with_whitespace_instructions = AgentConfig.objects.create(
            user=user,
            name="With whitespace instructions",
            llm_provider=provider,
            system_prompt="Keep this other prompt",
            autonomy_instructions="   ",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_non_empty_autonomy_is_trimmed_and_appended_without_trimming_prompt(self):
        AgentConfig = self.apps.get_model("nova", "AgentConfig")
        agent = AgentConfig.objects.get(pk=self.agent_with_instructions.pk)

        self.assertEqual(
            agent.system_prompt,
            "  Existing prompt  \n\nDecide alone.",
        )
        self.assertNotIn("autonomy_instructions", {
            field.name for field in AgentConfig._meta.get_fields()
        })

    def test_empty_autonomy_does_not_change_prompt(self):
        AgentConfig = self.apps.get_model("nova", "AgentConfig")
        agent = AgentConfig.objects.get(pk=self.agent_with_empty_instructions.pk)

        self.assertEqual(agent.system_prompt, "Keep this prompt")

    def test_whitespace_only_autonomy_does_not_change_prompt(self):
        AgentConfig = self.apps.get_model("nova", "AgentConfig")
        agent = AgentConfig.objects.get(pk=self.agent_with_whitespace_instructions.pk)

        self.assertEqual(agent.system_prompt, "Keep this other prompt")
