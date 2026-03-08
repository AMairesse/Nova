# nova/tests/models/test_provider.py
from django.core.exceptions import ValidationError
from django.test import override_settings

from nova.models.Provider import LLMProvider, ProviderType, check_and_create_system_provider
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_agent


class ProviderModelsTest(BaseTestCase):
    def test_llm_provider_creation(self):
        """
        Test LLMProvider model creation with valid parameters.
        Ensures that provider objects are created correctly with all required fields.
        """
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OLLAMA,
            model="test-model",
            max_context_tokens=4096,
        )
        self.assertEqual(provider.user, self.user)
        self.assertEqual(provider.name, "Test Provider")
        self.assertEqual(provider.provider_type, ProviderType.OLLAMA)

    def test_llm_provider_clean_valid(self):
        """
        Test LLMProvider validation with valid parameters.
        Verifies that clean() accepts properly configured providers.
        """
        provider = LLMProvider(
            user=self.user,
            name="Test",
            provider_type=ProviderType.OLLAMA,
            model="test",
            max_context_tokens=1024,
        )
        provider.full_clean()  # Should not raise

    def test_llm_provider_clean_too_small_context(self):
        """
        Test LLMProvider validation with insufficient context tokens.
        Ensures that max_context_tokens must be at least 512.
        """
        provider = LLMProvider(
            user=self.user,
            name="Test",
            provider_type=ProviderType.OLLAMA,
            model="test",
            max_context_tokens=256,
        )
        with self.assertRaises(ValidationError):
            provider.full_clean()

    def test_llm_provider_str(self):
        """
        Test LLMProvider string representation.
        Verifies that __str__ returns provider name and type.
        """
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OLLAMA,
            model="test-model",
        )
        self.assertEqual(str(provider), "Test Provider (ollama)")

    def test_compute_validation_fingerprint_changes_with_configuration(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Fingerprint Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )

        original_fingerprint = provider.compute_validation_fingerprint()
        provider.model = "gpt-4.1-mini"
        changed_fingerprint = provider.compute_validation_fingerprint()

        self.assertNotEqual(original_fingerprint, changed_fingerprint)

    def test_save_marks_provider_validation_stale_after_configuration_change(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Validated Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated successfully.",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 15},
                    "vision": {"status": "pass", "message": "ok", "latency_ms": 20},
                },
            }
        )

        provider.model = "gpt-4.1-mini"
        provider.save(update_fields=["model"])
        provider.refresh_from_db()

        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.STALE)
        self.assertEqual(provider.capability_profile_summary, "")

    def test_save_clears_running_validation_state_after_configuration_change(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Testing Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )
        provider.mark_validation_started(
            task_id="task-123",
            requested_fingerprint=provider.compute_validation_fingerprint(),
        )

        provider.model = "gpt-4.1-mini"
        provider.save(update_fields=["model"])
        provider.refresh_from_db()

        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.UNTESTED)
        self.assertEqual(provider.validation_task_id, "")
        self.assertEqual(provider.validation_requested_fingerprint, "")

    def test_save_clears_capability_profile_after_configuration_change(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Snapshot Provider",
            provider_type=ProviderType.OPENROUTER,
            model="openrouter/model-a",
            api_key="secret-a",
        )
        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "OpenRouter models API",
                "inputs": {"text": "pass", "pdf": "pass"},
                "outputs": {"text": "pass"},
                "operations": {"chat": "pass"},
                "limits": {"context_tokens": 100000},
                "model_state": {},
            }
        )

        provider.model = "openrouter/model-b"
        provider.save(update_fields=["model"])
        provider.refresh_from_db()

        self.assertEqual(provider.capability_profile, {})
        self.assertIsNone(provider.metadata_checked_at)

    def test_declared_and_verified_capabilities_merge_into_effective_profile(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Merged Provider",
            provider_type=ProviderType.OPENROUTER,
            model="openrouter/model-a",
            api_key="secret-a",
        )

        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "OpenRouter models API",
                "inputs": {"text": "pass", "image": "pass", "pdf": "unsupported", "audio": "unknown"},
                "outputs": {"text": "pass", "image": "unknown", "audio": "unknown"},
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": "pass",
                    "vision": "unsupported",
                    "structured_output": "pass",
                    "reasoning": "unknown",
                    "image_generation": "unknown",
                    "audio_generation": "unknown",
                },
                "limits": {"context_tokens": 128000},
                "model_state": {},
            }
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated successfully.",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 14},
                    "vision": {"status": "pass", "message": "vision ok", "latency_ms": 16},
                },
            }
        )
        provider.refresh_from_db()

        self.assertEqual(provider.known_image_input_status, "pass")
        self.assertEqual(provider.known_pdf_input_status, "unsupported")
        text_input_item = next(item for item in provider.capability_input_items if item["key"] == "text")
        image_input_item = next(item for item in provider.capability_input_items if item["key"] == "image")
        text_output_item = next(item for item in provider.capability_output_items if item["key"] == "text")
        self.assertEqual(text_input_item["source"], "merged")
        self.assertEqual(image_input_item["source"], "merged")
        self.assertEqual(text_output_item["source"], "merged")
        self.assertEqual(provider.get_capability_result("tools")["status"], "pass")
        self.assertEqual(provider.get_capability_result("tools")["source"], "merged")
        self.assertEqual(provider.get_capability_result("vision")["status"], "pass")
        self.assertEqual(provider.get_capability_result("vision")["source"], "verified")
        self.assertEqual(provider.capability_profile_schema_version, 1)

    def test_effective_capabilities_are_unknown_when_profile_fingerprint_is_stale(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Stale Fingerprint Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated successfully.",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 14},
                    "vision": {"status": "unsupported", "message": "no vision", "latency_ms": 16},
                },
            }
        )

        provider.model = "gpt-4.1-mini"

        self.assertEqual(provider.known_vision_capability_status, "")
        self.assertFalse(provider.is_capability_explicitly_unavailable("vision"))

    def test_unknown_input_capabilities_do_not_block_runtime_gating(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Unknown Capability Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )

        self.assertEqual(provider.capability_profile_schema_version, None)
        self.assertEqual(provider.known_image_input_status, "")
        self.assertFalse(provider.is_input_modality_explicitly_unavailable("image"))
        self.assertFalse(provider.is_capability_explicitly_unavailable("vision"))

    def test_check_and_create_system_provider_no_settings(self):
        """
        Test system provider creation function with no settings.
        Ensures that check_and_create_system_provider() handles missing settings gracefully.
        """
        # With no settings, the function should not create providers and not raise errors
        from nova.models.Provider import check_and_create_system_provider

        check_and_create_system_provider()  # Should not raise

    @override_settings(
        OLLAMA_SERVER_URL='http://ollama:11434',
        OLLAMA_MODEL_NAME='llama3.2',
        OLLAMA_CONTEXT_LENGTH=4096,
    )
    def test_check_and_create_system_provider_ollama_create(self):
        """
        Test system provider creation for Ollama when settings are configured.
        Ensures that a new system provider is created when none exists.
        """
        # Ensure no system provider exists initially
        LLMProvider.objects.filter(user=None, name='System - Ollama').delete()

        # Call the function - should create the provider
        check_and_create_system_provider()

        # Verify provider was created
        provider = LLMProvider.objects.filter(user=None, name='System - Ollama').first()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.provider_type, ProviderType.OLLAMA)
        self.assertEqual(provider.model, 'llama3.2')
        self.assertEqual(provider.base_url, 'http://ollama:11434')
        self.assertEqual(provider.max_context_tokens, 4096)

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_ollama_delete_used(self):
        """
        Test system provider deletion prevention for Ollama when provider is in use.
        Ensures that used system provider is not deleted when settings become unavailable.
        """
        # Create a system provider and an agent that uses it
        provider = LLMProvider.objects.create(
            user=None,
            name='System - Ollama',
            provider_type=ProviderType.OLLAMA,
            model='llama3.2',
            base_url='http://ollama:11434',
            max_context_tokens=4096,
        )
        create_agent(self.user, provider)

        # Call the function - should not delete because provider is used
        with self.assertLogs("nova.models.Provider") as logger:
            check_and_create_system_provider()

        # Check that a warning was created
        self.assertListEqual(logger.output, [
            """WARNING:nova.models.Provider:WARNING: OLLAMA_SERVER_URL or OLLAMA_MODEL_NAME not set, but a system
                       provider exists and is being used by at least one agent."""
        ])

        # Verify provider still exists
        provider = LLMProvider.objects.filter(user=None, name='System - Ollama').first()
        self.assertIsNotNone(provider)

    @override_settings(
        OLLAMA_SERVER_URL='http://ollama:11434',
        OLLAMA_MODEL_NAME='llama3.2',
        OLLAMA_CONTEXT_LENGTH=4096,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_ollama_no_change(self):
        """
        Test system provider behavior when settings match existing provider.
        Ensures that no changes are made when provider already matches settings.
        """
        # Create a provider that matches the settings
        LLMProvider.objects.create(
            user=None,
            name='System - Ollama',
            provider_type=ProviderType.OLLAMA,
            model='llama3.2',
            base_url='http://ollama:11434',
            max_context_tokens=4096,
        )

        # Call the function - should not change anything
        check_and_create_system_provider()

        # Verify provider still exists and matches
        provider = LLMProvider.objects.filter(user=None, name='System - Ollama').first()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.model, 'llama3.2')
        self.assertEqual(provider.base_url, 'http://ollama:11434')
        self.assertEqual(provider.max_context_tokens, 4096)

    @override_settings(
        OLLAMA_SERVER_URL='http://ollama:11434',
        OLLAMA_MODEL_NAME='llama3.2',
        OLLAMA_CONTEXT_LENGTH=4096,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_ollama_update(self):
        """
        Test system provider update for Ollama when settings change.
        Ensures that existing system provider is updated when settings differ.
        """
        # Create a provider with different settings
        LLMProvider.objects.create(
            user=None,
            name='System - Ollama',
            provider_type=ProviderType.OLLAMA,
            model='old-model',
            base_url='http://old-url:11434',
            max_context_tokens=2048,
        )

        # Call the function - should update the provider
        check_and_create_system_provider()

        # Refresh and verify provider was updated
        provider = LLMProvider.objects.filter(user=None, name='System - Ollama').first()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.model, 'llama3.2')
        self.assertEqual(provider.base_url, 'http://ollama:11434')
        self.assertEqual(provider.max_context_tokens, 4096)

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_ollama_delete_unused(self):
        """
        Test system provider deletion for Ollama when settings are removed.
        Ensures that unused system provider is deleted when settings become unavailable.
        """
        # Create a system provider
        LLMProvider.objects.create(
            user=None,
            name='System - Ollama',
            provider_type=ProviderType.OLLAMA,
            model='llama3.2',
            base_url='http://ollama:11434',
            max_context_tokens=4096,
        )

        # Call the function - should delete the unused provider
        check_and_create_system_provider()

        # Verify provider was deleted
        self.assertFalse(LLMProvider.objects.filter(user=None, name='System - Ollama').exists())

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL='http://llamacpp:8080',
        LLAMA_CPP_MODEL='qwen/qwen3-8B-GGUF',
        LLAMA_CPP_CTX_SIZE=4096,
    )
    def test_check_and_create_system_provider_llamacpp_create(self):
        """
        Test system provider creation for llama.cpp when settings are configured.
        Ensures that a new llama.cpp system provider is created when none exists.
        """
        # Ensure no system provider exists initially
        LLMProvider.objects.filter(user=None, name='System - llama.cpp').delete()

        # Call the function - should create the provider
        check_and_create_system_provider()

        # Verify provider was created
        provider = LLMProvider.objects.filter(user=None, name='System - llama.cpp').first()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.provider_type, ProviderType.LLAMA_CPP)
        self.assertEqual(provider.model, 'qwen/qwen3-8B-GGUF')
        self.assertEqual(provider.base_url, 'http://llamacpp:8080')
        self.assertEqual(provider.max_context_tokens, 4096)

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL='http://llamacpp:8080',
        LLAMA_CPP_MODEL='qwen/qwen3-8B-GGUF',
        LLAMA_CPP_CTX_SIZE=4096,
    )
    def test_check_and_create_system_provider_llamacpp_update(self):
        """
        Test system provider update for llama.cpp when settings change.
        Ensures that existing system provider is updated when settings differ.
        """
        # Create a provider with different settings
        LLMProvider.objects.create(
            user=None,
            name='System - llama.cpp',
            provider_type=ProviderType.LLAMA_CPP,
            model='old-model',
            base_url='http://old-url:8000',
            max_context_tokens=2048,
        )

        # Call the function - should update the provider
        check_and_create_system_provider()

        # Refresh and verify provider was updated
        provider = LLMProvider.objects.filter(user=None, name='System - llama.cpp').first()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.model, 'qwen/qwen3-8B-GGUF')
        self.assertEqual(provider.base_url, 'http://llamacpp:8080')
        self.assertEqual(provider.max_context_tokens, 4096)

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_llamacpp_delete_unused(self):
        """
        Test system provider deletion for llama.cpp when settings are removed.
        Ensures that unused system provider is deleted when settings become unavailable.
        """
        # Create a system provider
        LLMProvider.objects.create(
            user=None,
            name='System - llama.cpp',
            provider_type=ProviderType.LLAMA_CPP,
            model='qwen/qwen3-8B-GGUF',
            base_url='http://llamacpp:8080',
            max_context_tokens=4096,
        )

        # Call the function - should delete the unused provider
        check_and_create_system_provider()

        # Verify provider was deleted
        self.assertFalse(LLMProvider.objects.filter(user=None, name='System - llama.cpp').exists())

    @override_settings(
        OLLAMA_SERVER_URL=None,
        OLLAMA_MODEL_NAME=None,
        OLLAMA_CONTEXT_LENGTH=None,
        LLAMA_CPP_SERVER_URL=None,
        LLAMA_CPP_MODEL=None,
        LLAMA_CPP_CTX_SIZE=None,
    )
    def test_check_and_create_system_provider_llamacpp_delete_used(self):
        """
        Test system provider deletion prevention for llama.cpp when provider is in use.
        Ensures that used system provider is not deleted when settings become unavailable.
        """
        # Create a system provider and an agent that uses it
        provider = LLMProvider.objects.create(
            user=None,
            name='System - llama.cpp',
            provider_type=ProviderType.LLAMA_CPP,
            model='qwen/qwen3-8B-GGUF',
            base_url='http://llamacpp:8080',
            max_context_tokens=4096,
        )
        create_agent(self.user, provider)

        # Call the function - should not delete because provider is used
        with self.assertLogs("nova.models.Provider") as logger:
            check_and_create_system_provider()

        # Check that a warning was created
        self.assertListEqual(logger.output, [
            """WARNING:nova.models.Provider:WARNING: LLAMA_CPP_SERVER_URL or LLAMA_CPP_MODEL not set, but a system
                       provider exists and is being used by at least one agent."""
        ])

        # Verify provider still exists
        provider = LLMProvider.objects.filter(user=None, name='System - llama.cpp').first()
        self.assertIsNotNone(provider)
