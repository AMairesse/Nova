# user_settings/views/agent.py
from __future__ import annotations
from django.contrib.auth.decorators import login_required

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, reverse, get_object_or_404
from django.views.decorators.csrf import csrf_protect
from django.views.generic import ListView

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider
from nova.models.UserObjects import UserProfile
from user_settings.forms import AgentForm
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
class AgentListView(LoginRequiredMixin, UserOwnedQuerySetMixin, ListView):
    model = AgentConfig
    template_name = "user_settings/agent_list.html"
    context_object_name = "agents"
    paginate_by = 20

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/agent_table.html"]
        return super().get_template_names()

    # ----------------------------- NEW --------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Retrieve—or create if missing—the profile for this user
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        ctx["default_agent_id"] = (
            profile.default_agent_id if profile.default_agent_id else None
        )

        # Check if user has access to any providers (including system providers)
        has_providers = LLMProvider.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).exists()
        ctx["has_providers"] = has_providers

        return ctx


# ---------------------------------------------------------------------------#
#  CREATE / UPDATE base                                                      #
# ---------------------------------------------------------------------------#
class _AgentBaseView(DashboardRedirectMixin, LoginRequiredMixin):
    """
    Custom save logic is required to inject the user before the first `.save()`
    and to handle many-to-many relations.
    """
    model = AgentConfig
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    dashboard_tab = "agents"

    def form_valid(self, form):
        is_new = form.instance.pk is None

        # 1) main object
        obj = form.save(commit=False)
        if is_new:
            obj.user = self.request.user
        obj.save()

        # 2) many-to-many
        if hasattr(form, "save_m2m"):
            form.save_m2m()

        self.object = obj
        return HttpResponseRedirect(self.get_success_url())


class AgentCreateView(_AgentBaseView, OwnerCreateView):
    pass


class AgentUpdateView(_AgentBaseView, OwnerUpdateView):
    pass


class AgentDeleteView(  # type: ignore[misc]
    DashboardRedirectMixin, LoginRequiredMixin, OwnerDeleteView
):
    model = AgentConfig
    template_name = "user_settings/agent_confirm_delete.html"
    dashboard_tab = "agents"


@csrf_protect
@login_required
def make_default_agent(request, agent_id):
    agent = get_object_or_404(AgentConfig, id=agent_id, user=request.user)
    if agent:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.default_agent = agent
        profile.save()
    return redirect(reverse('user_settings:dashboard') + '#pane-agents')
