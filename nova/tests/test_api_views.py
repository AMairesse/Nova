# nova/tests/test_api_views.py
from __future__ import annotations

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from nova.api.views import QuestionAnswerView
from nova.models.Provider import ProviderType
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_agent, create_provider


class QuestionAnswerViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        provider = create_provider(self.user, provider_type=ProviderType.OPENAI)
        agent = create_agent(self.user, provider=provider)
        self.profile.default_agent = agent
        self.profile.save()

    def _view(self):
        return QuestionAnswerView.as_view()

    def test_get_requires_authentication(self):
        request = self.factory.get("/api/ask/")
        response = self._view()(request)
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_get_usage_ok(self):
        request = self.factory.get("/api/ask/")
        force_authenticate(request, user=self.user)
        response = self._view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("usage", response.data)
        self.assertIn("message", response.data)
        self.assertIn("payload_format", response.data["usage"])

    def test_post_invalid_payload_returns_400_with_errors(self):
        request = self.factory.post("/api/ask/", data={}, format="json")
        force_authenticate(request, user=self.user)
        response = self._view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("question", response.data)

    def test_post_missing_default_agent_returns_400(self):
        self.profile.default_agent = None
        self.profile.save()
        request = self.factory.post(
            "/api/ask/", data={"question": "Hi?"}, format="json"
        )
        force_authenticate(request, user=self.user)
        response = self._view()(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "User has no default agent")

    def test_post_valid_payload_success(self):
        class FakeLLMAgent:
            def __init__(self, user, thread_id, agent_config):
                self.user = user
                self.thread_id = thread_id
                self.agent_config = agent_config

            @classmethod
            async def create(cls, user, thread_id, agent_config):
                return cls(user, thread_id, agent_config)

            async def ainvoke(self, question):
                return "Answer"

        with patch("nova.api.views.LLMAgent", FakeLLMAgent):
            request = self.factory.post(
                "/api/ask/", data={"question": "Hi?"}, format="json"
            )
            force_authenticate(request, user=self.user)
            response = self._view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["question"], "Hi?")
        self.assertEqual(response.data["answer"], "Answer")

    def test_post_llm_error_returns_500(self):
        class FailingLLMAgent:
            def __init__(self, user, thread_id, agent_config):
                self.user = user
                self.thread_id = thread_id
                self.agent_config = agent_config

            @classmethod
            async def create(cls, user, thread_id, agent_config):
                return cls(user, thread_id, agent_config)

            def ainvoke(self, question):
                raise RuntimeError("boom")

        with patch("nova.api.views.LLMAgent", FailingLLMAgent):
            request = self.factory.post(
                "/api/ask/", data={"question": "Hi?"}, format="json"
            )
            force_authenticate(request, user=self.user)
            response = self._view()(request)
        self.assertEqual(
            response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        self.assertIn("LLM error", response.data.get("detail", ""))

    def test_post_agent_creation_failure_returns_500(self):
        with patch("nova.api.views.LLMAgent.create") as mock_create:
            mock_create.side_effect = RuntimeError("failed to build agent")
            request = self.factory.post(
                "/api/ask/", data={"question": "Hi?"}, format="json"
            )
            force_authenticate(request, user=self.user)
            response = self._view()(request)
        self.assertEqual(
            response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        self.assertIn("LLM error", response.data.get("detail", ""))
