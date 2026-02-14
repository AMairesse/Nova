from rest_framework import serializers


class QuestionSerializer(serializers.Serializer):
    question = serializers.CharField(required=True)


class SignalInboundSerializer(serializers.Serializer):
    message = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)
    selected_agent_id = serializers.IntegerField(required=False)
    transport = serializers.CharField(required=False, allow_blank=True, default="signal_gateway")
    external_message_id = serializers.CharField(required=False, allow_blank=True, max_length=200)
