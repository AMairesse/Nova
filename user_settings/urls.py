# user_settings/urls.py
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
    make_default_agent,
    bootstrap_defaults,
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
from user_settings.views.memory import MemorySettingsView
from user_settings.views.memory_browser import MemoryItemsListView
from user_settings.views.api_token import GenerateAPITokenView, DeleteAPITokenView
from user_settings.views.scheduled_tasks import (
    scheduled_tasks_list,
    scheduled_task_create,
    scheduled_task_edit,
    scheduled_task_delete,
    scheduled_task_toggle_active,
    scheduled_task_run_now,
    scheduled_task_clear_error,
    scheduled_task_cron_preview,
)

app_name = 'user_settings'

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
    path("agents/make_default/<int:agent_id>/", make_default_agent, name="make_default_agent"),
    path("agents/bootstrap-defaults/", bootstrap_defaults, name="agents-bootstrap-defaults"),
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
    path("general/api-token/generate/", GenerateAPITokenView.as_view(), name="api-token-generate"),
    path("general/api-token/delete/", DeleteAPITokenView.as_view(), name="api-token-delete"),
]

urlpatterns += [
    # Memory
    path("memory/", MemorySettingsView.as_view(), name="memory"),
    path("memory/items/", MemoryItemsListView.as_view(), name="memory-items"),
]

urlpatterns += [
    # Tasks
    path("tasks/", scheduled_tasks_list, name="tasks"),
    path("tasks/add/", scheduled_task_create, name="task_create"),
    path("tasks/<int:pk>/edit/", scheduled_task_edit, name="task_edit"),
    path("tasks/<int:pk>/delete/", scheduled_task_delete, name="task_delete"),
    path("tasks/<int:pk>/toggle-active/", scheduled_task_toggle_active, name="task_toggle_active"),
    path("tasks/<int:pk>/run-now/", scheduled_task_run_now, name="task_run_now"),
    path("tasks/<int:pk>/clear-error/", scheduled_task_clear_error, name="task_clear_error"),
    path("tasks/cron-preview/", scheduled_task_cron_preview, name="task_cron_preview"),

    # Legacy scheduled-tasks aliases (kept for compatibility)
    path("scheduled-tasks/", scheduled_tasks_list, name="scheduled_tasks"),
    path("scheduled-tasks/add/", scheduled_task_create, name="scheduled_task_create"),
    path("scheduled-tasks/<int:pk>/edit/", scheduled_task_edit, name="scheduled_task_edit"),
    path("scheduled-tasks/<int:pk>/delete/", scheduled_task_delete, name="scheduled_task_delete"),
    path("scheduled-tasks/<int:pk>/toggle-active/", scheduled_task_toggle_active, name="scheduled_task_toggle_active"),
    path("scheduled-tasks/<int:pk>/run-now/", scheduled_task_run_now, name="scheduled_task_run_now"),
    path("scheduled-tasks/<int:pk>/clear-error/", scheduled_task_clear_error, name="scheduled_task_clear_error"),
    path("scheduled-tasks/cron-preview/", scheduled_task_cron_preview, name="scheduled_task_cron_preview"),
]
