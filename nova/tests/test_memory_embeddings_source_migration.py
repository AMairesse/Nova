from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MemoryEmbeddingsSourceMigrationTests(TransactionTestCase):
    migrate_from = ("nova", "0062_task_execution_trace")
    migrate_to = ("nova", "0063_memory_embeddings_source_and_system_state")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        UserParameters = old_apps.get_model("nova", "UserParameters")

        self.user_custom = User.objects.create(username="migration-custom")
        self.user_system = User.objects.create(username="migration-system")

        UserParameters.objects.create(
            user=self.user_custom,
            memory_embeddings_enabled=True,
            memory_embeddings_url="https://custom.example.com/v1",
            memory_embeddings_model="embed-custom",
        )
        UserParameters.objects.create(
            user=self.user_system,
            memory_embeddings_enabled=False,
            memory_embeddings_url="https://ignored.example.com/v1",
            memory_embeddings_model="embed-ignored",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_enabled_user_with_url_becomes_custom(self):
        UserParameters = self.apps.get_model("nova", "UserParameters")
        params = UserParameters.objects.get(user_id=self.user_custom.id)

        self.assertEqual(params.memory_embeddings_source, "custom")

    def test_all_other_existing_rows_become_system(self):
        UserParameters = self.apps.get_model("nova", "UserParameters")
        params = UserParameters.objects.get(user_id=self.user_system.id)

        self.assertEqual(params.memory_embeddings_source, "system")

    def test_new_rows_default_to_system(self):
        User = self.apps.get_model("auth", "User")
        UserParameters = self.apps.get_model("nova", "UserParameters")

        user = User.objects.create(username="migration-new-default")
        params = UserParameters.objects.create(user=user)

        self.assertEqual(params.memory_embeddings_source, "system")
