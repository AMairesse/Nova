# user_settings/views/provider.py
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import ListView

from nova.models.models import LLMProvider
from nova.utils import check_and_create_system_provider
from user_settings.forms import LLMProviderForm
from user_settings.mixins import (
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
    DashboardRedirectMixin,
    SystemReadonlyMixin,
)


# ---------------------------------------------------------------------------#
#  List                                                                      #
# ---------------------------------------------------------------------------#
class ProviderListView(LoginRequiredMixin, ListView):
    model = LLMProvider
    template_name = "user_settings/provider_list.html"
    context_object_name = "providers"
    paginate_by = 5

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/provider_table.html"]
        return super().get_template_names()

    def get_queryset(self):
        # Ensure the system provider exists
        check_and_create_system_provider()
        # Return the user's providers and the system's one
        return LLMProvider.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by('user', 'name')


# ---------------------------------------------------------------------------#
#  CRUD                                                                      #
# ---------------------------------------------------------------------------#
class ProviderCreateView(
    DashboardRedirectMixin, LoginRequiredMixin, OwnerCreateView
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderUpdateView(  # type: ignore[misc]
    DashboardRedirectMixin, LoginRequiredMixin, OwnerUpdateView, SystemReadonlyMixin
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderDeleteView(  # type: ignore[misc]
    DashboardRedirectMixin, LoginRequiredMixin, OwnerDeleteView, SystemReadonlyMixin
):
    model = LLMProvider
    template_name = "user_settings/provider_confirm_delete.html"
    dashboard_tab = "providers"
