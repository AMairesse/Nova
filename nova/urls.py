# nova/urls.py
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog
from .views.main_views import index, message_list, create_thread, delete_thread, add_message, running_tasks
from .views.user_config_views import UserConfigView
from .views.provider_views import create_provider, edit_provider, delete_provider
from .views.agent_views import create_agent, edit_agent, delete_agent, make_default_agent
from .views.files_views import sidebar_panel_view, file_list, file_upload, FileDeleteView, FileMoveView
from .views.tools_views import create_tool, edit_tool, delete_tool, configure_tool, test_tool_connection
from .views.security_views import csrf_token
from .views.health import healthz
from django.conf import settings

urlpatterns = [
    # Main views
    path("", index, name="index"),
    path("message-list/", message_list, name="message_list"),
    path("create-thread/", create_thread, name="create_thread"), 
    path("delete-thread/<int:thread_id>/", delete_thread, name="delete_thread"), 
    path("add-message/", add_message, name="add_message"),
    path("running-tasks/<int:thread_id>/", running_tasks, name="running_tasks"),
    # User config
    path("user-config/", UserConfigView.as_view(), name="user_config"),
    # Provider management
    path("create-provider/", create_provider, name="create_provider"),
    path("provider/edit/<int:provider_id>/", edit_provider, name="edit_provider"),
    path('provider/delete/<int:provider_id>/', delete_provider, name='delete_provider'),
    # Agent management
    path("create-agent/", create_agent, name="create_agent"),
    path("agent/edit/<int:agent_id>/", edit_agent, name="edit_agent"),
    path('agent/delete/<int:agent_id>/', delete_agent, name='delete_agent'),
    path("agent/make_default_agent/<int:agent_id>/", make_default_agent, name="make_default_agent"),
    # Tool management
    path("create-tool/", create_tool, name="create_tool"),
    path("tool/edit/<int:tool_id>/", edit_tool, name="edit_tool"),
    path('tool/delete/<int:tool_id>/', delete_tool, name='delete_tool'),
    path("tool/configure/<int:tool_id>/", configure_tool, name="configure_tool"),
    path("tool/test-connection/<int:tool_id>/", test_tool_connection, name="test_tool_connection"),
    # API
    path('api/', include('nova.api.urls')),
    # Authentication views
    path("accounts/", include("django.contrib.auth.urls")),
    # Admin
    path('admin/', admin.site.urls),
    # i18n
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("api/csrf/", csrf_token, name="api-csrf"),
    ]

# File management
urlpatterns += [
    path('files/sidebar-panel/', sidebar_panel_view, name='files_sidebar_panel'),
    path('files/list/<int:thread_id>/', file_list, name='file_list'),
    path('files/upload/<int:thread_id>/', file_upload, name='file_upload'),
    path('files/delete/<int:file_id>/', FileDeleteView.as_view(), name='file_delete'),
    path('files/move/<int:file_id>/', FileMoveView.as_view(), name='file_move'),
]

# Add healthcheck only in DEBUG mode
if settings.DEBUG:
    urlpatterns += [
        path('healthz/', healthz, name='healthz'),
    ]