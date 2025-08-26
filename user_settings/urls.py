from django.urls import path

from user_settings.views.provider import (
    ProviderListView,
    ProviderCreateView,
    ProviderUpdateView,
    ProviderDeleteView,
)

app_name = "user_settings"

urlpatterns = [
    # LLM Providers
    path(
        "providers/",
        ProviderListView.as_view(),
        name="providers",
    ),
    path(
        "providers/add/",
        ProviderCreateView.as_view(),
        name="provider-add",
    ),
    path(
        "providers/<int:pk>/edit/",
        ProviderUpdateView.as_view(),
        name="provider-edit",
    ),
    path(
        "providers/<int:pk>/delete/",
        ProviderDeleteView.as_view(),
        name="provider-delete",
    ),
]
