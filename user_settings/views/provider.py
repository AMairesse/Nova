# user_settings/views/provider.py
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from nova.models.models import LLMProvider
from user_settings.forms import LLMProviderForm
from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
    DashboardRedirectMixin,
)


# ---------------------------------------------------------------------------#
#  List                                                                      #
# ---------------------------------------------------------------------------#
class ProviderListView(LoginRequiredMixin, UserOwnedQuerySetMixin, ListView):
    model = LLMProvider
    template_name = "user_settings/provider_list.html"
    context_object_name = "providers"
    paginate_by = 5

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/provider_table.html"]
        return super().get_template_names()


# ---------------------------------------------------------------------------#
#  CRUD                                                                      #
# ---------------------------------------------------------------------------#
class ProviderCreateView(  # type: ignore[misc] – CBV signature
    DashboardRedirectMixin, LoginRequiredMixin, OwnerCreateView
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderUpdateView(  # type: ignore[misc]
    DashboardRedirectMixin, LoginRequiredMixin, OwnerUpdateView
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderDeleteView(  # type: ignore[misc]
    DashboardRedirectMixin, LoginRequiredMixin, OwnerDeleteView
):
    model = LLMProvider
    template_name = "user_settings/provider_confirm_delete.html"
    dashboard_tab = "providers"
