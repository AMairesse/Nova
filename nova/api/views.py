# nova/api/views.py
import uuid
from typing import Any, Dict

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from asgiref.sync import async_to_sync

from .serializers import QuestionSerializer
from nova.models.models import UserProfile
from nova.llm.llm_agent import LLMAgent


class QuestionAnswerView(APIView):
    """
    Very small Q-A endpoint.

    – GET  → usage information
    – POST → run the LLM synchronously and return the answer
    """

    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------ #
    #  GET – usage                                                       #
    # ------------------------------------------------------------------ #
    def get(self, request, *args, **kwargs):
        usage: Dict[str, Any] = {
            "message": "Welcome to the Question-Answer API",
            "usage": {
                "method": "POST",
                "content_type": "application/json",
                "payload_format": {"question": "string (required)"},
                "example_payload": {"question": "Who are you and what can you do ?"},
            },
        }
        return Response(usage)

    # ------------------------------------------------------------------ #
    #  POST – answer a question                                          #
    # ------------------------------------------------------------------ #
    def post(self, request, *args, **kwargs):
        serializer = QuestionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)

        question: str = serializer.validated_data["question"]

        # Find the user's default agent
        agent_config = UserProfile.objects.get(user=request.user).default_agent
        if not agent_config:
            return Response(
                {"detail": "User has no default agent"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create the agent
        llm_agent = async_to_sync(LLMAgent.create)(request.user, None, agent_config)

        try:
            answer = async_to_sync(llm_agent.ainvoke)(question)
        except Exception as exc:
            return Response(
                {"detail": f"LLM error: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = {"question": question, "answer": answer}
        return Response(response_data, status=status.HTTP_200_OK)
