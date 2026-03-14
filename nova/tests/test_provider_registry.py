from django.test import SimpleTestCase

from nova.models.Provider import LLMProvider, ProviderType
from nova.providers import (
    get_provider_adapter,
    get_provider_defaults,
    normalize_multimodal_content_for_provider,
)


class ProviderRegistryTests(SimpleTestCase):
    def test_registry_returns_distinct_adapters_for_openai_and_openrouter(self):
        openai_adapter = get_provider_adapter(ProviderType.OPENAI)
        openrouter_adapter = get_provider_adapter(ProviderType.OPENROUTER)

        self.assertIsNot(openai_adapter, openrouter_adapter)

    def test_registry_exposes_openrouter_defaults(self):
        defaults = get_provider_defaults(ProviderType.OPENROUTER)

        self.assertEqual(defaults.default_base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(defaults.default_max_context_tokens, 100000)
        self.assertTrue(defaults.api_key_required)
        self.assertTrue(defaults.supports_model_catalog)

    def test_registry_marks_openai_as_manual_model_provider(self):
        defaults = get_provider_defaults(ProviderType.OPENAI)

        self.assertFalse(defaults.supports_model_catalog)

    def test_registry_exposes_mistral_catalog_defaults(self):
        defaults = get_provider_defaults(ProviderType.MISTRAL)

        self.assertEqual(defaults.default_base_url, "https://api.mistral.ai/v1")
        self.assertTrue(defaults.supports_model_catalog)

    def test_registry_normalizes_mistral_pdf_inputs_to_document_url(self):
        provider = LLMProvider(
            name="Mistral Provider",
            provider_type=ProviderType.MISTRAL,
            model="mistral-small-latest",
            api_key="dummy",
        )

        normalized = normalize_multimodal_content_for_provider(
            provider,
            [
                {
                    "type": "text",
                    "text": "Read the PDF.",
                },
                {
                    "type": "file",
                    "source_type": "base64",
                    "data": "cGRm",
                    "mime_type": "application/pdf",
                    "filename": "brief.pdf",
                },
            ],
        )

        self.assertEqual(
            normalized,
            [
                {
                    "type": "text",
                    "text": "Read the PDF.",
                },
                {
                    "type": "document_url",
                    "document_url": "data:application/pdf;base64,cGRm",
                },
            ],
        )

    def test_registry_normalizes_mistral_images_to_string_image_urls(self):
        provider = LLMProvider(
            name="Mistral Provider",
            provider_type=ProviderType.MISTRAL,
            model="mistral-small-latest",
            api_key="dummy",
        )

        normalized = normalize_multimodal_content_for_provider(
            provider,
            [
                {
                    "type": "image",
                    "source_type": "base64",
                    "data": "aW1hZ2U=",
                    "mime_type": "image/png",
                    "filename": "diagram.png",
                }
            ],
        )

        self.assertEqual(
            normalized,
            [
                {
                    "type": "image_url",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                }
            ],
        )
