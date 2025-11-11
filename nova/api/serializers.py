from rest_framework import serializers


class QuestionSerializer(serializers.Serializer):
    question = serializers.CharField(required=True)
