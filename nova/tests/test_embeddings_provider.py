from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from nova.llm.embeddings import aget_embeddings_provider, get_embeddings_provider
from nova.models.UserObjects import UserParameters


User = get_user_model()


class EmbeddingsProviderResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="emb-user",
            email="emb-user@example.com",
            password="testpass123",
        )

    @override_settings(
        LLAMA_CPP_SERVER_URL="http://llamacpp:8080/v1",
        LLAMA_CPP_MODEL="qwen/qwen3-8B-GGUF",
        MEMORY_EMBEDDINGS_URL="http://env-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="env-model",
    )
    def test_sync_and_async_prefer_system_llamacpp(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_enabled = True
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.save(update_fields=[
            "memory_embeddings_enabled",
            "memory_embeddings_url",
            "memory_embeddings_model",
        ])

        sync_provider = get_embeddings_provider(user_id=self.user.id)
        async_provider = async_to_sync(aget_embeddings_provider)(user_id=self.user.id)

        self.assertIsNotNone(sync_provider)
        self.assertIsNotNone(async_provider)
        self.assertEqual(sync_provider.provider_type, "llama.cpp")
        self.assertEqual(async_provider.provider_type, "llama.cpp")
        self.assertEqual(sync_provider.base_url, "http://llamacpp:8080/v1")
        self.assertEqual(async_provider.base_url, "http://llamacpp:8080/v1")

    @override_settings(
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        MEMORY_EMBEDDINGS_URL="http://env-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="env-model",
    )
    def test_sync_and_async_prefer_user_provider_before_env_fallback(self):
        params, _ = UserParameters.objects.get_or_create(user=self.user)
        params.memory_embeddings_enabled = True
        params.memory_embeddings_url = "http://user-embed:8080/v1"
        params.memory_embeddings_model = "user-model"
        params.memory_embeddings_api_key = "secret-key"
        params.save(update_fields=[
            "memory_embeddings_enabled",
            "memory_embeddings_url",
            "memory_embeddings_model",
            "memory_embeddings_api_key",
        ])

        sync_provider = get_embeddings_provider(user_id=self.user.id)
        async_provider = async_to_sync(aget_embeddings_provider)(user_id=self.user.id)

        self.assertIsNotNone(sync_provider)
        self.assertIsNotNone(async_provider)
        self.assertEqual(sync_provider.provider_type, "custom_http")
        self.assertEqual(async_provider.provider_type, "custom_http")
        self.assertEqual(sync_provider.base_url, "http://user-embed:8080/v1")
        self.assertEqual(async_provider.base_url, "http://user-embed:8080/v1")
        self.assertEqual(sync_provider.model, "user-model")
        self.assertEqual(async_provider.model, "user-model")
        self.assertEqual(sync_provider.api_key, "secret-key")
        self.assertEqual(async_provider.api_key, "secret-key")

    @override_settings(
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        MEMORY_EMBEDDINGS_URL="http://env-embed:8080/v1",
        MEMORY_EMBEDDINGS_MODEL="env-model",
        MEMORY_EMBEDDINGS_API_KEY="env-key",
    )
    def test_sync_and_async_fallback_to_env_provider(self):
        sync_provider = get_embeddings_provider(user_id=self.user.id)
        async_provider = async_to_sync(aget_embeddings_provider)(user_id=self.user.id)

        self.assertIsNotNone(sync_provider)
        self.assertIsNotNone(async_provider)
        self.assertEqual(sync_provider.provider_type, "custom_http")
        self.assertEqual(async_provider.provider_type, "custom_http")
        self.assertEqual(sync_provider.base_url, "http://env-embed:8080/v1")
        self.assertEqual(async_provider.base_url, "http://env-embed:8080/v1")
        self.assertEqual(sync_provider.model, "env-model")
        self.assertEqual(async_provider.model, "env-model")
        self.assertEqual(sync_provider.api_key, "env-key")
        self.assertEqual(async_provider.api_key, "env-key")
