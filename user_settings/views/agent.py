from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView

from nova.models.models import Agent
from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
    DashboardRedirectMixin,
)
from user_settings.forms import AgentForm


class AgentListView(LoginRequiredMixin,
                    UserOwnedQuerySetMixin,
                    ListView):
    model = Agent
    template_name = "user_settings/agent_list.html"
    context_object_name = "agents"
    paginate_by = 20

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/agent_table.html"]
        return super().get_template_names()


class AgentCreateView(DashboardRedirectMixin, LoginRequiredMixin, OwnerCreateView):
    model = Agent
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    dashboard_tab = "agents"
    success_url = reverse_lazy("user_settings:dashboard")


class AgentUpdateView(DashboardRedirectMixin, LoginRequiredMixin, OwnerUpdateView):
    model = Agent
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    dashboard_tab = "agents"
    success_url = reverse_lazy("user_settings:dashboard")


class AgentDeleteView(DashboardRedirectMixin, LoginRequiredMixin, OwnerDeleteView):
    model = Agent
    template_name = "user_settings/agent_confirm_delete.html"
    dashboard_tab = "agents"
    success_url = reverse_lazy("user_settings:dashboard")
