from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from nova.llm.embeddings import (
    aget_embeddings_provider,
    get_embeddings_provider,
    get_resolved_embeddings_provider,
)
from nova.models.EmbeddingsSystemState import EmbeddingsSystemState
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters


User = get_user_model()


class EmbeddingsProviderResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="emb-user",
            email="emb-user@example.com",
            password="testpass123",
        )
        self._memory_delay = patch(
            "nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay"
        )
        self._conversation_delay = patch(
            "nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay"
        )
        self.mock_memory_delay = self._memory_delay.start()
        self.mock_conversation_delay = self._conversation_delay.start()
        self.addCleanup(self._memory_delay.stop)
        self.addCleanup(self._conversation_delay.stop)

    def test_user_parameters_default_to_system_source(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)

        self.assertEqual(params.memory_embeddings_source, MemoryEmbeddingsSource.SYSTEM)

    @override_settings(
        LLAMA_CPP_SERVER_URL="http://llamacpp:8080/v1",
        LLAMA_CPP_MODEL="qwen/qwen3-8B-GGUF",
        MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="system-model",
    )
    def test_custom_source_wins_and_ignores_llama_cpp(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_source = MemoryEmbeddingsSource.CUSTOM
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.memory_embeddings_api_key = "secret-key"
        params.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
                "memory_embeddings_api_key",
            ]
        )

        sync_provider = get_embeddings_provider(user_id=self.user.id)
        async_provider = async_to_sync(aget_embeddings_provider)(user_id=self.user.id)

        self.assertIsNotNone(sync_provider)
        self.assertIsNotNone(async_provider)
        self.assertEqual(sync_provider.base_url, "http://user-embed:8080/v1")
        self.assertEqual(async_provider.base_url, "http://user-embed:8080/v1")
        self.assertEqual(sync_provider.model, "user-model")
        self.assertEqual(async_provider.model, "user-model")
        self.assertEqual(sync_provider.api_key, "secret-key")
        self.assertEqual(async_provider.api_key, "secret-key")

    @override_settings(
        LLAMA_CPP_SERVER_URL="http://llamacpp:8080/v1",
        LLAMA_CPP_MODEL="qwen/qwen3-8B-GGUF",
        MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="system-model",
        MEMORY_EMBEDDINGS_API_KEY="env-key",
    )
    def test_system_source_uses_memory_embeddings_settings_only(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_source = MemoryEmbeddingsSource.SYSTEM
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.memory_embeddings_api_key = "user-key"
        params.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
                "memory_embeddings_api_key",
            ]
        )

        sync_provider = get_embeddings_provider(user_id=self.user.id)
        async_provider = async_to_sync(aget_embeddings_provider)(user_id=self.user.id)

        self.assertIsNotNone(sync_provider)
        self.assertIsNotNone(async_provider)
        self.assertEqual(sync_provider.base_url, "http://system-embed:8080/v1")
        self.assertEqual(async_provider.base_url, "http://system-embed:8080/v1")
        self.assertEqual(sync_provider.model, "system-model")
        self.assertEqual(async_provider.model, "system-model")
        self.assertEqual(sync_provider.api_key, "env-key")
        self.assertEqual(async_provider.api_key, "env-key")

    @override_settings(
        LLAMA_CPP_SERVER_URL="http://llamacpp:8080/v1",
        LLAMA_CPP_MODEL="qwen/qwen3-8B-GGUF",
        MEMORY_EMBEDDINGS_URL=None,
        MEMORY_EMBEDDINGS_MODEL=None,
    )
    def test_system_source_does_not_fallback_to_custom_or_llama_cpp(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_source = MemoryEmbeddingsSource.SYSTEM
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
            ]
        )

        self.assertIsNone(get_embeddings_provider(user_id=self.user.id))
        self.assertIsNone(async_to_sync(aget_embeddings_provider)(user_id=self.user.id))

    @override_settings(
        MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="system-model",
    )
    def test_custom_source_does_not_fallback_to_system_when_unconfigured(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_source = MemoryEmbeddingsSource.CUSTOM
        params.memory_embeddings_url = ""
        params.memory_embeddings_model = ""
        params.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
            ]
        )

        self.assertIsNone(get_embeddings_provider(user_id=self.user.id))
        self.assertIsNone(async_to_sync(aget_embeddings_provider)(user_id=self.user.id))

    @override_settings(
        MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="system-model",
    )
    def test_disabled_source_returns_none(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_source = MemoryEmbeddingsSource.DISABLED
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
            ]
        )

        self.assertIsNone(get_embeddings_provider(user_id=self.user.id))
        self.assertIsNone(async_to_sync(aget_embeddings_provider)(user_id=self.user.id))


class EmbeddingsSystemBackfillTests(TestCase):
    def setUp(self):
        self.system_user = User.objects.create_user(
            username="system-user",
            email="system@example.com",
            password="testpass123",
        )
        self.custom_user = User.objects.create_user(
            username="custom-user",
            email="custom@example.com",
            password="testpass123",
        )
        self.disabled_user = User.objects.create_user(
            username="disabled-user",
            email="disabled@example.com",
            password="testpass123",
        )

        UserParameters.objects.get_or_create(user=self.system_user)

        custom_params, _ = UserParameters.objects.get_or_create(user=self.custom_user)
        custom_params.memory_embeddings_source = MemoryEmbeddingsSource.CUSTOM
        custom_params.memory_embeddings_url = "http://user-embed:8080/v1"
        custom_params.save(
            update_fields=["memory_embeddings_source", "memory_embeddings_url"]
        )

        disabled_params, _ = UserParameters.objects.get_or_create(user=self.disabled_user)
        disabled_params.memory_embeddings_source = MemoryEmbeddingsSource.DISABLED
        disabled_params.save(update_fields=["memory_embeddings_source"])

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    def test_first_system_provider_appearance_backfills_system_users_only(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        get_resolved_embeddings_provider(user_id=self.system_user.id)
        self.assertEqual(EmbeddingsSystemState.objects.get(singleton_key=1).provider_available, False)

        with override_settings(
            MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
            MEMORY_EMBEDDINGS_MODEL="system-model",
        ):
            get_resolved_embeddings_provider(user_id=self.system_user.id)

        mocked_memory_delay.assert_called_once_with(self.system_user.id)
        mocked_conversation_delay.assert_called_once_with(self.system_user.id)
        state = EmbeddingsSystemState.objects.get(singleton_key=1)
        self.assertTrue(state.provider_available)
        self.assertTrue(bool(state.last_backfill_fingerprint))

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    def test_system_provider_reappearance_with_same_fingerprint_requeues_after_absence(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        with override_settings(
            MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
            MEMORY_EMBEDDINGS_MODEL="system-model",
        ):
            get_resolved_embeddings_provider(user_id=self.system_user.id)

        mocked_memory_delay.reset_mock()
        mocked_conversation_delay.reset_mock()

        get_resolved_embeddings_provider(user_id=self.system_user.id)

        with override_settings(
            MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
            MEMORY_EMBEDDINGS_MODEL="system-model",
        ):
            get_resolved_embeddings_provider(user_id=self.system_user.id)

        mocked_memory_delay.assert_called_once_with(self.system_user.id)
        mocked_conversation_delay.assert_called_once_with(self.system_user.id)

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    def test_system_provider_fingerprint_change_requeues_backfill(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        with override_settings(
            MEMORY_EMBEDDINGS_URL="http://system-embed:8080/v1",
            MEMORY_EMBEDDINGS_MODEL="system-model",
        ):
            get_resolved_embeddings_provider(user_id=self.system_user.id)

        mocked_memory_delay.reset_mock()
        mocked_conversation_delay.reset_mock()

        with override_settings(
            MEMORY_EMBEDDINGS_URL="http://system-embed-v2:8080/v1",
            MEMORY_EMBEDDINGS_MODEL="system-model-v2",
        ):
            get_resolved_embeddings_provider(user_id=self.system_user.id)

        mocked_memory_delay.assert_called_once_with(self.system_user.id)
        mocked_conversation_delay.assert_called_once_with(self.system_user.id)
