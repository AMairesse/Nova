from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.reverse import reverse
from .views import QuestionAnswerView, SignalInboundView


class APIRootView(APIView):
    def get(self, request):
        return Response({
            'ask-question': reverse('ask-question', request=request),
            'signal-inbound': reverse('signal-inbound', request=request)
        })


urlpatterns = [
    path('', APIRootView.as_view(), name='api-root'),
    path('ask/', QuestionAnswerView.as_view(), name='ask-question'),
    path('channels/signal/inbound/', SignalInboundView.as_view(), name='signal-inbound'),
]