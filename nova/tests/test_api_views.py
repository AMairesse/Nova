# nova/tests/test_api_views.py
from django.test import TestCase
from django.contrib.auth import get_user_model

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from nova.api.views import QuestionAnswerView


class QuestionAnswerViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="tester", email="tester@example.com", password="pass"
        )

    def test_get_requires_authentication(self):
        request = self.factory.get("/api/qa/")
        response = QuestionAnswerView.as_view()(request)

        self.assertIn(response.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

    def test_get_usage_ok(self):
        request = self.factory.get("/api/qa/")
        force_authenticate(request, user=self.user)
        response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("usage", response.data)
        self.assertIn("message", response.data)
        self.assertIn("payload_format", response.data["usage"])

    def test_post_invalid_payload_returns_400(self):
        request = self.factory.post("/api/qa/", data={}, format="json")
        force_authenticate(request, user=self.user)
        response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.data)

    def test_post_valid_payload_success(self):
        class FakeLLMAgent:
            def __init__(self, user, thread_id):
                self.user = user
                self.thread_id = thread_id

            def invoke(self, question):
                return "This is the answer"

        from unittest.mock import patch
        with patch("nova.api.views.LLMAgent", FakeLLMAgent):
            request = self.factory.post("/api/qa/", data={"question": "Hi?"}, format="json")
            force_authenticate(request, user=self.user)
            response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("question"), "Hi?")
        self.assertEqual(response.data.get("answer"), "This is the answer")

    def test_post_llm_error_returns_500(self):
        class FailingLLMAgent:
            def __init__(self, user, thread_id):
                pass

            def invoke(self, question):
                raise RuntimeError("boom")

        from unittest.mock import patch
        with patch("nova.api.views.LLMAgent", FailingLLMAgent):
            request = self.factory.post("/api/qa/", data={"question": "Hi?"}, format="json")
            force_authenticate(request, user=self.user)
            response = QuestionAnswerView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("LLM error", response.data.get("detail", ""))
