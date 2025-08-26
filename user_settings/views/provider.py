from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView
from nova.models.models import LLMProvider

from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
)
from user_settings.forms import LLMProviderForm


class ProviderListView(LoginRequiredMixin,
                       UserOwnedQuerySetMixin,
                       ListView):
    model = LLMProvider
    template_name = "user_settings/provider_list.html"
    context_object_name = "providers"
    paginate_by = 5

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/provider_table.html"]
        return super().get_template_names()


class ProviderCreateView(LoginRequiredMixin, OwnerCreateView):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    success_url = reverse_lazy("user_settings:providers")


class ProviderUpdateView(LoginRequiredMixin, OwnerUpdateView):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    success_url = reverse_lazy("user_settings:providers")


class ProviderDeleteView(LoginRequiredMixin, OwnerDeleteView):
    model = LLMProvider
    template_name = "user_settings/provider_confirm_delete.html"
    success_url = reverse_lazy("user_settings:providers")
