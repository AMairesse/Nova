from django.urls import path

from user_settings.views.dashboard import DashboardView
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
from user_settings.views.tool import (
    ToolListView,
    ToolCreateView,
    ToolUpdateView,
    ToolDeleteView,
    ToolConfigureView,
    tool_test_connection
)
from user_settings.views.general import GeneralSettingsView

app_name = "user_settings"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
]

urlpatterns += [
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

urlpatterns += [
    # Tools
    path("tools/", ToolListView.as_view(), name="tools"),
    path("tools/add/", ToolCreateView.as_view(), name="tool-add"),
    path("tools/<int:pk>/edit/", ToolUpdateView.as_view(), name="tool-edit"),
    path("tools/<int:pk>/delete/", ToolDeleteView.as_view(), name="tool-delete"),
    path("tools/<int:pk>/configure/", ToolConfigureView.as_view(), name="tool-configure"),
    path("tools/<int:pk>/test/", tool_test_connection, name="tool-test"),
]

urlpatterns += [
    # General
    path("general/", GeneralSettingsView.as_view(), name="general"),
]
