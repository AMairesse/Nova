# nova/tests/test_api.py
"""
Smoke-tests for the Question-Answer REST API.

External optional dependencies from LangChain / LangGraph are
stubbed so the nova.llm_agent module can be imported even when the
real libraries are absent in the test environment.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
#  Make the optional “langchain* / langgraph*” stacks inert                   #
# --------------------------------------------------------------------------- #
import sys
import types

def _stub(mod_name: str, attrs: dict[str, object] | None = None) -> None:
    """
    Register a dummy module so that `import` statements do not fail.

    attrs – optional mapping of attribute → object to expose.
    """
    parts = mod_name.split(".")
    for i in range(1, len(parts) + 1):
        sub = ".".join(parts[:i])
        if sub not in sys.modules:
            sys.modules[sub] = types.ModuleType(sub)

    if attrs:
        mod = sys.modules[mod_name]
        for k, v in attrs.items():
            setattr(mod, k, v)

# Core helpers expected by nova.llm_agent
_stub("langchain_core.messages", {"HumanMessage": object, "AIMessage": object})
_stub("langchain_core.tools", {"StructuredTool": object})
_stub("langchain_core")

# Chat model back-ends
_stub("langchain_mistralai.chat_models", {"ChatMistralAI": object})
_stub("langchain_mistralai")
_stub("langchain_ollama.chat_models", {"ChatOllama": object})
_stub("langchain_ollama")
_stub("langchain_openai.chat_models", {"ChatOpenAI": object})
_stub("langchain_openai")

# LangGraph shortcuts
_stub("langgraph.checkpoint.memory", {"MemorySaver": object})
_stub("langgraph.checkpoint")
_stub("langgraph.prebuilt", {"create_react_agent": lambda *a, **kw: object()})
_stub("langgraph")

# --------------------------------------------------------------------------- #
#  Regular test imports                                                       #
# --------------------------------------------------------------------------- #
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock

from ..models import UserProfile, LLMProvider, Agent


class QuestionAnswerViewTests(APITestCase):
    """
    Smoke-tests for the async Question-Answer API view.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpass")

        # Minimal provider + agent so LLMAgent initialisation succeeds
        self.llm_provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type="mistral",
            model="mistral-large-latest",
            api_key="test_key",
        )
        self.agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.llm_provider,
            system_prompt="You are a test agent",
        )
        profile, created = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = self.agent
        profile.save()
        
        self.url = reverse("ask-question")

    # ------------------------------------------------------------------ #
    #  GET /api/ask/ – usage helper                                      #
    # ------------------------------------------------------------------ #
    def test_get_question_answer_view_authenticated(self):
        self.client.login(username="testuser", password="testpass")

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected = {
            "message": "Welcome to the Question-Answer API",
            "usage": {
                "method": "POST",
                "content_type": "application/json",
                "payload_format": {"question": "string (required)"},
                "example_payload": {"question": "What is your question ?"},
            },
        }
        self.assertEqual(response.json(), expected)

    def test_get_question_answer_view_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ------------------------------------------------------------------ #
    #  POST /api/ask/ – happy path                                       #
    # ------------------------------------------------------------------ #
    @patch("nova.llm_agent.LLMAgent.invoke")
    @patch("nova.llm_agent.LLMAgent.create_llm_agent")
    def test_post_question_answer_view_authenticated(
        self, mock_create_llm, mock_invoke
    ):
        """
        The view should return the LLM answer as-is when the user is
        authenticated. Heavy LLM calls are monkey-patched so the test
        runs instantly and without external dependencies.
        """
        self.client.login(username="testuser", password="testpass")

        mock_create_llm.return_value = MagicMock()
        mock_invoke.return_value = "Paris"

        data = {"question": "What is the capital of France?"}
        response = self.client.post(self.url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["question"], data["question"])
        self.assertEqual(response.data["answer"], "Paris")

    # ------------------------------------------------------------------ #
    #  POST /api/ask/ – authentication required                          #
    # ------------------------------------------------------------------ #
    def test_post_question_answer_view_unauthenticated(self):
        response = self.client.post(self.url, {"question": "Ping"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
