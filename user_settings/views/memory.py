from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.views.generic import UpdateView
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from nova.models.UserObjects import UserInfo
from user_settings.forms import UserInfoForm
from user_settings.mixins import DashboardRedirectMixin


class MemorySettingsView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
    SuccessMessageMixin,
    UpdateView
):
    """
    View and edit user memory information stored in Markdown format.
    """
    model = UserInfo
    form_class = UserInfoForm
    template_name = "user_settings/memory_form.html"
    success_message = _("Memory updated successfully")
    dashboard_tab = "memory"
    success_url = reverse_lazy("user_settings:dashboard")

    # Ensure every user has a UserInfo row
    def get_object(self, queryset=None):
        obj, _ = UserInfo.objects.get_or_create(user=self.request.user)
        return obj

    # HTMX: if ?partial=1, return only the fragment
    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/memory_form.html"]
        return [self.template_name]

    def form_valid(self, form):
        redirect_response = super().form_valid(form)

        if self.request.headers.get("HX-Request") == "true":
            resp = HttpResponse(status=204)
            resp["HX-Refresh"] = "true"
            return resp

        return redirect_response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['help_text'] = _(
            "Store your personal information in Markdown format. "
            "Use headings (# Theme Name) to organize different topics. "
            "Note: '# global_user_preferences' is a special theme that is always available "
            "and cannot be deleted - it contains your essential preferences that are "
            "automatically shared with agents when the memory tool is enabled."
        )
        return context
