# nova/urls.py
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog
from nova.views.thread_views import (
    index, message_list, create_thread, delete_thread,
    add_message, load_more_threads, summarize_thread, confirm_summarize_thread
)
from nova.views.continuous_views import (
    continuous_home,
    continuous_days,
    continuous_day,
    continuous_messages,
    continuous_add_message,
    continuous_regenerate_summary,
)
from nova.views.task_views import execution_trace, running_tasks
from nova.views.files_views import (
    sidebar_panel_view, file_list,
    file_download_url, file_upload, FileDeleteView, artifact_publish, artifact_content
)
from nova.views.interaction_views import (
    answer_interaction, cancel_interaction
)
from nova.views.pwa_views import service_worker
from nova.views.push_views import push_config, push_subscriptions
from nova.views.security_views import csrf_token
from nova.views.health import healthz
from nova.views.webapp_views import serve_webapp, webapps_list, preview_webapp, delete_webapp

urlpatterns = [
    # Main views
    path("", index, name="index"),
    path("message-list/", message_list, name="message_list"),
    path("create-thread/", create_thread, name="create_thread"),
    path("delete-thread/<int:thread_id>/", delete_thread, name="delete_thread"),
    path("summarize-thread/<int:thread_id>/", summarize_thread, name="summarize_thread"),
    path("confirm-summarize-thread/<int:thread_id>/", confirm_summarize_thread, name="confirm_summarize_thread"),
    path("add-message/", add_message, name="add_message"),
    path("load-more-threads/", load_more_threads, name="load_more_threads"),
    path("running-tasks/<int:thread_id>/", running_tasks, name="running_tasks"),
    path("tasks/<int:task_id>/execution-trace/", execution_trace, name="task_execution_trace"),

    # Continuous discussion mode
    path("continuous/", continuous_home, name="continuous_home"),
    path("continuous/days/", continuous_days, name="continuous_days"),
    path("continuous/day/<str:day>/", continuous_day, name="continuous_day"),
    path("continuous/messages/", continuous_messages, name="continuous_messages"),
    path("continuous/add-message/", continuous_add_message, name="continuous_add_message"),
    path("continuous/regenerate-summary/", continuous_regenerate_summary, name="continuous_regenerate_summary"),

    # API
    path('api/', include('nova.api.urls')),
    # Authentication views
    path("accounts/", include("django.contrib.auth.urls")),
    # Admin
    path('supernova-admin/', admin.site.urls),
    # Service worker
    path('sw.js', service_worker, name='service_worker'),
    # i18n
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("api/csrf/", csrf_token, name="api-csrf"),
    path("healthz/", healthz, name="healthz"),
    path("push/config/", push_config, name="push_config"),
    path("push/subscriptions/", push_subscriptions, name="push_subscriptions"),
    ]

# File management
urlpatterns += [
    path('files/sidebar-panel/', sidebar_panel_view, name='files_sidebar_panel'),
    path('files/list/<int:thread_id>/', file_list, name='file_list'),
    path('files/download-url/<int:file_id>/', file_download_url, name='file_download_url'),
    path('files/upload/<int:thread_id>/', file_upload, name='file_upload'),
    path('files/delete/<int:file_id>/', FileDeleteView.as_view(), name='file_delete'),
    path('files/publish-artifact/<int:artifact_id>/', artifact_publish, name='artifact_publish'),
    path('files/artifact-content/<int:artifact_id>/', artifact_content, name='artifact_content'),
]

# Users' interactions from agents
urlpatterns += [
    path('interactions/<int:interaction_id>/answer/', answer_interaction, name='interaction_answer'),
    path('interactions/<int:interaction_id>/cancel/', cancel_interaction, name='interaction_cancel'),
]

# Web apps sidebar listing (server-rendered partial)
urlpatterns += [
    path('apps/list/<int:thread_id>/', webapps_list, name='webapps_list'),
]

# Web apps
urlpatterns += [
    path('apps/preview/<int:thread_id>/<slug:slug>/', preview_webapp, name='preview_webapp'),
    path('apps/delete/<int:thread_id>/<slug:slug>/', delete_webapp, name='delete_webapp'),
    path('apps/<slug:slug>/', serve_webapp, name='serve_webapp_root'),
    path('apps/<slug:slug>/<path:path>/', serve_webapp, name='serve_webapp_file'),
]

# User settings
urlpatterns += [
     path("settings/", include("user_settings.urls")),
]
