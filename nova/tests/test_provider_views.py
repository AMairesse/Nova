# nova/tests/test_config_views.py
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from nova.models.models import LLMProvider, ProviderType, Agent


class ProviderViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pass"
        )

    # ------------------------- create_provider -------------------------

    def test_create_provider_requires_login(self):
        url = reverse("create_provider")
        resp = self.client.post(
            url,
            data={
                "name": "My Prov",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_create_provider_creates_record_and_redirects(self):
        self.client.login(username="alice", password="pass")
        url = reverse("create_provider")
        resp = self.client.post(
            url,
            data={
                "name": "My Prov",
                "provider_type": ProviderType.OPENAI,
                "model": "gpt-4o-mini",
                "api_key": "",            # should become None
                "base_url": "   ",        # should become None
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=providers")

        qs = LLMProvider.objects.filter(user=self.user, name="My Prov")
        self.assertTrue(qs.exists())
        prov = qs.get()
        self.assertEqual(prov.provider_type, ProviderType.OPENAI)
        self.assertEqual(prov.model, "gpt-4o-mini")
        self.assertIsNone(prov.api_key)
        self.assertIsNone(prov.base_url)

    # ------------------------- edit_provider -------------------------

    def _create_provider(self, **overrides) -> LLMProvider:
        defaults = {
            "user": self.user,
            "name": "Prov",
            "provider_type": ProviderType.MISTRAL,
            "model": "mistral-small-latest",
            "api_key": "secret",
            "base_url": "https://api.example.com",
        }
        defaults.update(overrides)
        return LLMProvider.objects.create(**defaults)

    def test_edit_provider_requires_login(self):
        prov = self._create_provider()
        url = reverse("edit_provider", args=[prov.id])
        resp = self.client.post(url, data={"name": "New name", "provider_type": ProviderType.OLLAMA})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_edit_provider_404_for_non_owner(self):
        prov = self._create_provider()
        self.client.login(username="bob", password="pass")
        url = reverse("edit_provider", args=[prov.id])
        resp = self.client.post(url, data={"name": "Hacked", "provider_type": ProviderType.OLLAMA})
        self.assertEqual(resp.status_code, 404)

    def test_edit_provider_partial_update_and_redirect(self):
        prov = self._create_provider()
        self.client.login(username="alice", password="pass")
        url = reverse("edit_provider", args=[prov.id])

        # Post empty model/api_key so they should be preserved; base_url present but empty => cleared
        resp = self.client.post(
            url,
            data={
                "name": "Renamed",
                "provider_type": ProviderType.OLLAMA,
                "model": "   ",      # keep original
                "api_key": "",       # keep original
                "base_url": "  ",    # clear to None
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=providers")

        prov.refresh_from_db()
        self.assertEqual(prov.name, "Renamed")
        self.assertEqual(prov.provider_type, ProviderType.OLLAMA)
        self.assertEqual(prov.model, "mistral-small-latest")  # unchanged
        self.assertEqual(prov.api_key, "secret")              # unchanged
        self.assertIsNone(prov.base_url)                      # cleared

    def test_edit_provider_updates_base_url_when_provided(self):
        prov = self._create_provider(base_url=None)
        self.client.login(username="alice", password="pass")
        url = reverse("edit_provider", args=[prov.id])

        resp = self.client.post(
            url,
            data={
                "name": "Prov",
                "provider_type": ProviderType.OPENAI,
                "model": "mistral-small-latest",  # unchanged by logic but acceptable
                "base_url": "https://new.example.org",
            },
        )
        self.assertEqual(resp.status_code, 302)
        prov.refresh_from_db()
        self.assertEqual(prov.provider_type, ProviderType.OPENAI)
        self.assertEqual(prov.base_url, "https://new.example.org")

    # ------------------------- delete_provider -------------------------

    def test_delete_provider_requires_login(self):
        prov = self._create_provider()
        url = reverse("delete_provider", args=[prov.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_delete_provider_404_for_non_owner(self):
        prov = self._create_provider()
        self.client.login(username="bob", password="pass")
        url = reverse("delete_provider", args=[prov.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 404)

    def test_delete_provider_deletes_agents_and_provider(self):
        prov = self._create_provider()
        # Create a couple of agents using this provider
        a1 = Agent.objects.create(user=self.user, name="A1", llm_provider=prov, system_prompt="x")
        a2 = Agent.objects.create(user=self.user, name="A2", llm_provider=prov, system_prompt="y")
        self.assertEqual(Agent.objects.filter(llm_provider=prov).count(), 2)

        self.client.login(username="alice", password="pass")
        url = reverse("delete_provider", args=[prov.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("user_config") + "?tab=providers")

        # Agents removed first, then provider removed
        self.assertFalse(Agent.objects.filter(pk__in=[a1.pk, a2.pk]).exists())
        self.assertFalse(LLMProvider.objects.filter(pk=prov.pk).exists())
