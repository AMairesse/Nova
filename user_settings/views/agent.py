from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
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


# ------------------------------------------------------------------ #
#  CREATE / UPDATE base                                              #
# ------------------------------------------------------------------ #
class _AgentBaseView(DashboardRedirectMixin, LoginRequiredMixin):
    model = Agent
    form_class = AgentForm
    template_name = "user_settings/agent_form.html"
    dashboard_tab = "agents"

    # -------- redirection vers le bon onglet ------------------------
    def get_success_url(self):
        base = reverse_lazy("user_settings:dashboard")
        return f"{base}?from={self.dashboard_tab}"

    # -------- sauvegarde complète ----------------------------------
    def form_valid(self, form):
        """
        Sauvegarde manuelle pour pouvoir injecter le user avant le
        premier `.save()`, puis gérer les Many-to-Many.
        """
        is_new = form.instance.pk is None

        # 1) objet principal
        obj = form.save(commit=False)
        if is_new:
            obj.user = self.request.user
        obj.save()

        # 2) relations Many-to-Many
        if hasattr(form, "save_m2m"):
            form.save_m2m()

        self.object = obj
        return HttpResponseRedirect(self.get_success_url())


class AgentCreateView(_AgentBaseView, OwnerCreateView):
    pass


class AgentUpdateView(_AgentBaseView, OwnerUpdateView):
    pass


class AgentDeleteView(DashboardRedirectMixin,
                      LoginRequiredMixin,
                      OwnerDeleteView):
    model = Agent
    template_name = "user_settings/agent_confirm_delete.html"
    dashboard_tab = "agents"

    def get_success_url(self):
        base = reverse_lazy("user_settings:dashboard")
        return f"{base}?from={self.dashboard_tab}"
