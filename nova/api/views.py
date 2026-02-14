from typing import Any, Dict

from asgiref.sync import async_to_sync
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from nova.continuous.message_ingest import ingest_continuous_user_message
from nova.llm.llm_agent import LLMAgent
from nova.models.AgentConfig import AgentConfig
from nova.models.UserObjects import UserProfile
from nova.tasks.tasks import run_ai_task_celery

from .serializers import QuestionSerializer, SignalInboundSerializer


class QuestionAnswerView(APIView):
    """
    Very small Q-A endpoint.

    – GET  → usage information
    – POST → run the LLM synchronously and return the answer
    """

    permission_classes = [IsAuthenticated]

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

    def post(self, request, *args, **kwargs):
        serializer = QuestionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        question: str = serializer.validated_data["question"]

        # Find the user's default agent
        agent_config = UserProfile.objects.get(user=request.user).default_agent
        if not agent_config:
            return Response(
                {"detail": "User has no default agent"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Create the agent
            llm_agent = async_to_sync(LLMAgent.create)(request.user, None, agent_config)
            answer = async_to_sync(llm_agent.ainvoke)(question)
        except Exception as exc:
            return Response(
                {"detail": f"LLM error: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = {"question": question, "answer": answer}
        return Response(response_data, status=status.HTTP_200_OK)


class SignalInboundView(APIView):
    """Token-authenticated inbound endpoint for Signal-like connectors.

    This endpoint feeds the shared continuous thread via the canonical ingestion
    service to keep channel integrations aligned with web continuous behavior.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SignalInboundSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        payload = serializer.validated_data

        selected_agent_id = payload.get("selected_agent_id")
        if selected_agent_id and not AgentConfig.objects.filter(id=selected_agent_id, user=request.user).exists():
            return Response(
                {"selected_agent_id": ["Invalid agent for this user."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ingest_result = ingest_continuous_user_message(
            user=request.user,
            message_text=payload["message"],
            run_ai_task=run_ai_task_celery,
            selected_agent_id=selected_agent_id,
            source_channel="signal",
            source_transport=(payload.get("transport") or "signal_gateway"),
            source_external_message_id=(payload.get("external_message_id") or None),
        )

        return Response(
            {
                "status": "OK",
                "thread_id": ingest_result.thread_id,
                "task_id": ingest_result.task_id,
                "message_id": ingest_result.message_id,
                "day_segment_id": ingest_result.day_segment_id,
                "day_label": ingest_result.day_label,
                "opened_new_day": ingest_result.opened_new_day,
            },
            status=status.HTTP_202_ACCEPTED,
        )
