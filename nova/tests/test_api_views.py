# nova/tests/test_api_views.py
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from nova.api.views import QuestionAnswerView
from nova.tests.base import BaseTestCase
from nova.models.models import Agent
from nova.models.Provider import ProviderType, LLMProvider


class QuestionAnswerViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        # Create an agent and set it as the default
        provider = LLMProvider.objects.create(user=self.user, name="Prov", provider_type=ProviderType.OPENAI,
                                              model="gpt-4o-mini", api_key="dummy")
        agent_config = Agent.objects.create(user=self.user, name="Agent A", llm_provider=provider, system_prompt="x")
        self.profile.default_agent = agent_config
        self.profile.save()

    def test_get_requires_authentication(self):
        request = self.factory.get("/api/ask/")
        response = QuestionAnswerView.as_view()(request)

        self.assertIn(response.status_code, {status.HTTP_401_UNAUTHORIZED,
                                             status.HTTP_403_FORBIDDEN})

    def test_get_usage_ok(self):
        request = self.factory.get("/api/ask/")
        force_authenticate(request, user=self.user)
        response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("usage", response.data)
        self.assertIn("message", response.data)
        self.assertIn("payload_format", response.data["usage"])

    def test_post_invalid_payload_returns_400(self):
        request = self.factory.post("/api/ask/", data={}, format="json")
        force_authenticate(request, user=self.user)
        response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.data)

    def test_post_valid_payload_success(self):
        class FakeLLMAgent:
            def __init__(self, user, thread_id, agent_config):
                self.user = None
                self.thread_id = None
                self.agent_config = None

            @classmethod
            async def create(cls, user, thread_id, agent_config):
                agent = cls(user, thread_id, agent_config)
                return agent

            async def ainvoke(self, question):
                return "This is the answer"

        with patch("nova.api.views.LLMAgent", FakeLLMAgent):
            request = self.factory.post("/api/ask/", data={"question": "Hi?"},
                                        format="json")
            force_authenticate(request, user=self.user)
            response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("question"), "Hi?")
        self.assertEqual(response.data.get("answer"), "This is the answer")

    def test_post_llm_error_returns_500(self):
        class FailingLLMAgent:
            def __init__(self, user, thread_id, agent_config):
                self.user = None
                self.thread_id = None
                self.agent_config = None

            @classmethod
            async def create(cls, user, thread_id, agent_config):
                agent = cls(user, thread_id, agent_config)
                return agent

            def ainvoke(self, question):
                raise RuntimeError("boom")

        with patch("nova.api.views.LLMAgent", FailingLLMAgent):
            request = self.factory.post("/api/ask/", data={"question": "Hi?"},
                                        format="json")
            force_authenticate(request, user=self.user)
            response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code,
                         status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("LLM error", response.data.get("detail", ""))
