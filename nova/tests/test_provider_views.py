from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.tests.factories import create_agent, create_provider, create_user


@override_settings(
    OLLAMA_SERVER_URL="http://ollama:11434",
    OLLAMA_MODEL_NAME="llama3",
    OLLAMA_CONTEXT_LENGTH=4096,
)
class ProviderViewsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="alice")
        self.other = create_user(username="bob")
        self.bootstrap_patcher = patch("user_settings.views.provider.check_and_create_system_provider")
        self.mock_bootstrap = self.bootstrap_patcher.start()
        self.client.login(username="alice", password="testpass123")

    def tearDown(self):
        self.bootstrap_patcher.stop()
        super().tearDown()

    @patch("user_settings.views.provider.check_and_create_system_provider")
    def test_list_partial_renders_fragment(self, mock_bootstrap):
        response = self.client.get(
            reverse("user_settings:providers"), {"partial": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "user_settings/fragments/provider_table.html"
        )
        mock_bootstrap.assert_called_once()

    def test_list_includes_user_and_public_providers(self):
        user_provider = create_provider(self.user, name="Owned")
        public_provider = LLMProvider.objects.create(
            user=None,
            name="Public Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
        )
        response = self.client.get(reverse("user_settings:providers"))
        providers = list(response.context["providers"])
        self.assertIn(user_provider, providers)
        self.assertIn(public_provider, providers)

    def test_create_provider_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data={
                "name": "My Prov",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_create_provider_creates_record_and_redirects(self):
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data={
                "name": "My Prov",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
                "api_key": "",
                "base_url": "   ",
                "max_context_tokens": 100000,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("user_settings:dashboard") + "#pane-providers",
        )
        provider = LLMProvider.objects.get(user=self.user, name="My Prov")
        self.assertEqual(provider.provider_type, ProviderType.OPENAI)
        self.assertEqual(provider.model, "gpt-4o-mini")
        self.assertIsNone(provider.api_key)
        self.assertIsNone(provider.base_url)

    def test_edit_provider_requires_owner(self):
        provider = create_provider(self.user)
        self.client.logout()
        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.id]),
            data={
                "name": "Attempt",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.client.login(username="bob", password="testpass123")
        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.id]),
            data={
                "name": "Hacked",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_edit_provider_preserves_api_key_and_clears_base_url(self):
        provider = create_provider(
            self.user, api_key="secret", base_url="https://old"
        )
        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.id]),
            data={
                "name": "Renamed",
                "provider_type": ProviderType.MISTRAL,
                "model": "mistral-small-latest",
                "api_key": "",
                "base_url": "",
                "max_context_tokens": 4096,
            },
        )
        self.assertEqual(response.status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.name, "Renamed")
        self.assertEqual(provider.provider_type, ProviderType.MISTRAL)
        self.assertEqual(provider.api_key, "secret")
        self.assertIsNone(provider.base_url)

    def test_edit_provider_updates_optional_fields(self):
        provider = create_provider(self.user, api_key=None, base_url=None)
        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.id]),
            data={
                "name": provider.name,
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
                "api_key": "new-key",
                "base_url": "https://new.example.org",
                "max_context_tokens": 8192,
            },
        )
        self.assertEqual(response.status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.api_key, "new-key")
        self.assertEqual(provider.base_url, "https://new.example.org")
        self.assertEqual(provider.max_context_tokens, 8192)

    def test_delete_provider_requires_owner(self):
        provider = create_provider(self.user)
        self.client.logout()
        response = self.client.post(
            reverse("user_settings:provider-delete", args=[provider.id])
        )
        self.assertEqual(response.status_code, 302)

        self.client.login(username="bob", password="testpass123")
        response = self.client.post(
            reverse("user_settings:provider-delete", args=[provider.id])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(LLMProvider.objects.filter(pk=provider.pk).exists())

    def test_delete_provider_cascades_agents(self):
        provider = create_provider(self.user)
        create_agent(self.user, provider=provider, name="Agent A")
        create_agent(self.user, provider=provider, name="Agent B")
        response = self.client.post(
            reverse("user_settings:provider-delete", args=[provider.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LLMProvider.objects.filter(pk=provider.pk).exists())
        self.assertFalse(AgentConfig.objects.filter(llm_provider=provider).exists())

    def test_system_provider_delete_is_noop(self):
        system_provider = LLMProvider.objects.create(
            user=None,
            name="System Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
        )
        response = self.client.post(
            reverse("user_settings:provider-delete", args=[system_provider.id])
        )
        # The view redirects back to dashboard but keeps the provider
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            LLMProvider.objects.filter(pk=system_provider.pk).exists()
        )
