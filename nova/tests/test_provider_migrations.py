from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OpenRouterProviderMigrationTests(TransactionTestCase):
    migrate_from = ("nova", "0054_llmprovider_validation_task_state")
    migrate_to = ("nova", "0055_llmprovider_openrouter")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        Provider = old_apps.get_model("nova", "LLMProvider")

        user = User.objects.create(username="migration-user")
        Provider.objects.create(
            user=user,
            name="OpenRouter Provider",
            provider_type="openai",
            model="google/gemini-2.5-flash",
            api_key="secret",
            base_url="https://openrouter.ai/api/v1",
            max_context_tokens=4096,
        )
        Provider.objects.create(
            user=user,
            name="OpenAI Provider",
            provider_type="openai",
            model="gpt-4o-mini",
            api_key="secret",
            base_url="https://api.openai.com/v1",
            max_context_tokens=4096,
        )
        Provider.objects.create(
            user=user,
            name="Custom Provider",
            provider_type="openai",
            model="custom-model",
            api_key="secret",
            base_url="https://custom.example.com/v1",
            max_context_tokens=4096,
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_converts_openrouter_urls_to_openrouter_provider_type(self):
        Provider = self.apps.get_model("nova", "LLMProvider")
        provider = Provider.objects.get(name="OpenRouter Provider")

        self.assertEqual(provider.provider_type, "openrouter")

    def test_migration_keeps_openai_native_urls_unchanged(self):
        Provider = self.apps.get_model("nova", "LLMProvider")
        provider = Provider.objects.get(name="OpenAI Provider")

        self.assertEqual(provider.provider_type, "openai")

    def test_migration_keeps_custom_urls_unchanged(self):
        Provider = self.apps.get_model("nova", "LLMProvider")
        provider = Provider.objects.get(name="Custom Provider")

        self.assertEqual(provider.provider_type, "openai")
