# nova/urls.py
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog
from .views.thread_views import (
    index, message_list, create_thread, delete_thread,
    add_message, load_more_threads, compact_thread
)
from .views.task_views import running_tasks
from .views.files_views import (
    sidebar_panel_view, file_list,
    file_download_url, file_upload, FileDeleteView, FileMoveView
)
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
    path("compact-thread/<int:thread_id>/", compact_thread, name="compact_thread"),
    path("load-more-threads/", load_more_threads, name="load_more_threads"),
    path("running-tasks/<int:thread_id>/", running_tasks, name="running_tasks"),
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
    path('files/download-url/<int:file_id>/', file_download_url, name='file_download_url'),
    path('files/upload/<int:thread_id>/', file_upload, name='file_upload'),
    path('files/delete/<int:file_id>/', FileDeleteView.as_view(), name='file_delete'),
    path('files/move/<int:file_id>/', FileMoveView.as_view(), name='file_move'),
]

# Add healthcheck only in DEBUG mode
if settings.DEBUG:
    urlpatterns += [
        path('healthz/', healthz, name='healthz'),
    ]

# User settings
urlpatterns += [
     path("settings/", include("user_settings.urls")),
]
