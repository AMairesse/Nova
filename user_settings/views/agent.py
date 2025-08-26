from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView

from nova.models.models import Agent
from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
)
from user_settings.forms import AgentForm


class AgentListView(LoginRequiredMixin,
                    UserOwnedQuerySetMixin,
                    ListView):
    model = Agent
    template_name = "user_settings/agent_list.html"
    context_object_name = "agents"
    paginate_by = 20
    ordering = ["name"]


class AgentCreateView(LoginRequiredMixin, OwnerCreateView):
    model = Agent
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    success_url = reverse_lazy("user_settings:agents")


class AgentUpdateView(LoginRequiredMixin, OwnerUpdateView):
    model = Agent
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    success_url = reverse_lazy("user_settings:agents")


class AgentDeleteView(LoginRequiredMixin, OwnerDeleteView):
    model = Agent
    template_name = "user_settings/agent_confirm_delete.html"
    success_url = reverse_lazy("user_settings:agents")
