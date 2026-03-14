from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase
from django.urls import reverse

from nova.models.UserObjects import UserProfile
from nova.tests.factories import create_agent, create_provider, create_user


class QuestionAnswerViewTests(TestCase):
    def setUp(self):
        self.user = create_user(username="api-user", email="api-user@example.com")
        self.client.force_login(self.user)
        self.url = reverse("ask-question")

    def test_get_returns_usage_payload(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "Welcome to the Question-Answer API")
        self.assertEqual(payload["usage"]["method"], "POST")

    def test_post_rejects_invalid_payload(self):
        response = self.client.post(
            self.url,
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("question", response.json())

    def test_post_requires_default_agent(self):
        UserProfile.objects.update_or_create(user=self.user, defaults={"default_agent": None})

        response = self.client.post(
            self.url,
            data={"question": "Hello?"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "User has no default agent")

    def test_post_returns_answer_from_default_agent(self):
        provider = create_provider(self.user, name="API Provider", model="gpt-4o-mini")
        agent_config = create_agent(self.user, provider, name="API Agent")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"default_agent": agent_config},
        )
        fake_agent = SimpleNamespace(ainvoke=AsyncMock(return_value="42"))

        with patch("nova.api.views.LLMAgent.create", new=AsyncMock(return_value=fake_agent)) as mocked_create:
            response = self.client.post(
                self.url,
                data={"question": "What is the answer?"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"question": "What is the answer?", "answer": "42"},
        )
        mocked_create.assert_awaited_once_with(self.user, None, agent_config)
        fake_agent.ainvoke.assert_awaited_once_with("What is the answer?")

    def test_post_surfaces_llm_failures(self):
        provider = create_provider(self.user, name="Broken Provider", model="gpt-4o-mini")
        agent_config = create_agent(self.user, provider, name="Broken Agent")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"default_agent": agent_config},
        )

        with patch("nova.api.views.LLMAgent.create", new=AsyncMock(side_effect=RuntimeError("boom"))):
            response = self.client.post(
                self.url,
                data={"question": "Will this fail?"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "LLM error: boom")
