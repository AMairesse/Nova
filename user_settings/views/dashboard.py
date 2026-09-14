# user_settings/views/dashboard.py
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider
from nova.models.UserObjects import UserProfile


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "user_settings/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        context["onboarding_providers"] = LLMProvider.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by("user", "name")
        context["onboarding_agents"] = AgentConfig.objects.filter(
            user=self.request.user
        ).select_related("llm_provider").order_by("name", "pk")
        context["onboarding_default_agent_id"] = profile.default_agent_id
        return context
