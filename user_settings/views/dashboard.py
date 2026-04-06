# user_settings/views/dashboard.py
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "user_settings/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check if user has memory tool enabled
        context['has_memory_tool'] = self.request.user.tools.filter(
            tool_subtype="memory",
            is_active=True
        ).exists()
        return context
