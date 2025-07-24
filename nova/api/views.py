# nova/api/views.py
import uuid
from typing import Any, Dict

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import QuestionSerializer, AnswerSerializer
from ..llm_agent import LLMAgent

TIMEOUT_S = 60  # reserved – not used for now (sync call)


class QuestionAnswerView(APIView):
    """
    Very small Q-A endpoint.

    – GET  → usage information
    – POST → run the LLM synchronously and return the answer

    We keep everything *synchronous* because DRF 3.14/3.15 still executes
    `APIView.dispatch()` in sync mode.  Using `async def` handlers therefore
    breaks when Django’s CSRF middleware tries to `await` a plain Response.
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
                "example_payload": {"question": "What is your question ?"},
            },
        }
        return Response(usage)

    # ------------------------------------------------------------------ #
    #  POST – answer a question                                          #
    # ------------------------------------------------------------------ #
    def post(self, request, *args, **kwargs):
        serializer = QuestionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        question: str = serializer.validated_data["question"]

        # Create the LLM agent (cheap)
        thread_id = str(uuid.uuid4())
        llm = LLMAgent(request.user, thread_id)

        # Run the LLM *synchronously*; unit-tests stub the call anyway.
        try:
            answer = llm.invoke(question)
        except Exception as exc:
            return Response(
                {"detail": f"LLM error: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = {"question": question, "answer": answer}
        return Response(AnswerSerializer(response_data).data, status=status.HTTP_200_OK)
