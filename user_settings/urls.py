from django.urls import path

from user_settings.views.provider import (
    ProviderListView,
    ProviderCreateView,
    ProviderUpdateView,
    ProviderDeleteView,
)

from user_settings.views.agent import (
    AgentListView,
    AgentCreateView,
    AgentUpdateView,
    AgentDeleteView,
)

app_name = "user_settings"

urlpatterns = [
    # LLM Providers
    path("providers/", ProviderListView.as_view(), name="providers"),
    path("providers/add/", ProviderCreateView.as_view(), name="provider-add"),
    path("providers/<int:pk>/edit/", ProviderUpdateView.as_view(), name="provider-edit"),
    path("providers/<int:pk>/delete/", ProviderDeleteView.as_view(), name="provider-delete"),
]

urlpatterns += [
    # Agents
    path("agents/", AgentListView.as_view(), name="agents"),
    path("agents/add/", AgentCreateView.as_view(), name="agent-add"),
    path("agents/<int:pk>/edit/", AgentUpdateView.as_view(), name="agent-edit"),
    path("agents/<int:pk>/delete/", AgentDeleteView.as_view(), name="agent-delete"),
]
