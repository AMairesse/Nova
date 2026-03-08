from django.test import SimpleTestCase

from nova.models.Provider import ProviderType
from user_settings.forms import LLMProviderForm


class ProviderFormTests(SimpleTestCase):
    def test_openrouter_defaults_come_from_provider_registry(self):
        form = LLMProviderForm(initial={"provider_type": ProviderType.OPENROUTER})

        self.assertEqual(form.initial["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(form.initial["max_context_tokens"], 100000)
        self.assertTrue(form.fields["api_key"].required)

    def test_ollama_api_key_is_not_required(self):
        form = LLMProviderForm(initial={"provider_type": ProviderType.OLLAMA})

        self.assertFalse(form.fields["api_key"].required)
