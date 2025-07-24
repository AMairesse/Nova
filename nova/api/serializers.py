from rest_framework import serializers

class QuestionSerializer(serializers.Serializer):
    question = serializers.CharField(required=True)

class AnswerSerializer(serializers.Serializer):
    question = serializers.CharField(read_only=True)
    answer = serializers.CharField(read_only=True)