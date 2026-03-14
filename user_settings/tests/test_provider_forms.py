from django.test import TestCase

from nova.models.Provider import ProviderType
from nova.tests.factories import create_user
from user_settings.forms import LLMProviderForm


class ProviderFormTests(TestCase):
    def test_openrouter_defaults_come_from_provider_registry(self):
        form = LLMProviderForm(initial={"provider_type": ProviderType.OPENROUTER})

        self.assertEqual(form.initial["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(form.initial["max_context_tokens"], 100000)
        self.assertTrue(form.fields["api_key"].required)

    def test_ollama_api_key_is_not_required(self):
        form = LLMProviderForm(initial={"provider_type": ProviderType.OLLAMA})

        self.assertFalse(form.fields["api_key"].required)

    def test_model_is_not_required(self):
        form = LLMProviderForm(
            data={
                "name": "Connection only",
                "provider_type": ProviderType.OPENROUTER,
                "model": "",
                "api_key": "dummy-secret",
                "base_url": "https://openrouter.ai/api/v1",
                "additional_config": "{}",
                "max_context_tokens": "4096",
            }
        )

        self.assertTrue(form.is_valid())

    def test_clearing_model_is_rejected_when_provider_is_used_by_agent(self):
        user = create_user(username="provider-form-user", email="provider-form@example.com")
        provider = user.llm_providers.create(
            name="In use",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )
        user.user_agents.create(
            name="Bound agent",
            llm_provider=provider,
            system_prompt="test",
        )

        form = LLMProviderForm(
            data={
                "name": provider.name,
                "provider_type": provider.provider_type,
                "model": "",
                "api_key": "",
                "base_url": "",
                "additional_config": "{}",
                "max_context_tokens": "4096",
            },
            instance=provider,
            user=user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("model", form.errors)
