# nova/tests/test_agent_views.py
"""
Smoke-tests for the CRUD views around the Agent model.

We do *not* test business logic executed by LLMAgent itself here, only the
web layer (permissions, redirects, HTTP methods).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models import Agent, LLMProvider

User = get_user_model()


class AgentViewsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("alice", password="pwd")
        self.other = User.objects.create_user("eve", password="pwd")

        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type="openai",
            model="gpt-3.5-turbo",
            api_key="dummy",
        )

        # Existing agent owned by alice
        self.agent = Agent.objects.create(
            user=self.user,
            name="Alice agent",
            llm_provider=self.provider,
            system_prompt="You are helpful",
        )

    # ------------------------------------------------------------------ #
    #  Login required                                                    #
    # ------------------------------------------------------------------ #
    def test_views_require_login(self):
        for url in [
            reverse("create_agent"),
            reverse("edit_agent", args=[self.agent.id]),
            reverse("delete_agent", args=[self.agent.id]),
        ]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp["Location"])

    # ------------------------------------------------------------------ #
    #  Create                                                            #
    # ------------------------------------------------------------------ #
    def _post_minimal_agent(self, user):
        self.client.login(username=user.username, password="pwd")
        return self.client.post(
            reverse("create_agent"),
            {
                "name": "New agent",
                "llm_provider": self.provider.id,
                "system_prompt": "Prompt",
            },
            follow=True,
        )

    def test_create_agent_success(self):
        resp = self._post_minimal_agent(self.user)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Agent.objects.filter(name="New agent", user=self.user).exists())

    # ------------------------------------------------------------------ #
    #  Edit protection                                                   #
    # ------------------------------------------------------------------ #
    def test_cannot_edit_someone_elses_agent(self):
        other_agent = Agent.objects.create(
            user=self.other,
            name="Eve agent",
            llm_provider=self.provider,
            system_prompt="Prompt",
        )
        self.client.login(username=self.user.username, password="pwd")
        resp = self.client.get(reverse("edit_agent", args=[other_agent.id]))
        # The view should return 404 (object filtered by user) or 403.
        self.assertIn(resp.status_code, (403, 404))

    # ------------------------------------------------------------------ #
    #  Delete must be POST                                               #
    # ------------------------------------------------------------------ #
    def test_delete_requires_post(self):
        self.client.login(username=self.user.username, password="pwd")
        url = reverse("delete_agent", args=[self.agent.id])

        # GET should be rejected
        resp_get = self.client.get(url)
        self.assertIn(resp_get.status_code, (403, 405))

        # Valid POST deletes the agent
        resp_post = self.client.post(url, follow=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertFalse(Agent.objects.filter(pk=self.agent.id).exists())

    # ------------------------------------------------------------------ #
    #  Ownership enforced for delete                                     #
    # ------------------------------------------------------------------ #
    def test_cannot_delete_other_users_agent(self):
        other_agent = Agent.objects.create(
            user=self.other,
            name="Eve agent",
            llm_provider=self.provider,
            system_prompt="Prompt",
        )
        self.client.login(username=self.user.username, password="pwd")
        resp = self.client.post(reverse("delete_agent", args=[other_agent.id]))
        self.assertIn(resp.status_code, (403, 404))
        self.assertTrue(Agent.objects.filter(pk=other_agent.id).exists())
