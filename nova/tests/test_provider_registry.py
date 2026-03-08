from django.test import SimpleTestCase

from nova.models.Provider import ProviderType
from nova.providers import get_provider_adapter, get_provider_defaults


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
