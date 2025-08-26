from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from nova.models.models import UserParameters
from user_settings.forms import UserParametersForm


class GeneralSettingsView(LoginRequiredMixin,
                          SuccessMessageMixin,
                          UpdateView):
    model = UserParameters
    form_class = UserParametersForm
    template_name = "user_settings/general_form.html"
    success_message = "Settings saved successfully"
    success_url = reverse_lazy("user_settings:dashboard")

    # ensure every user has a row
    def get_object(self, queryset=None):
        obj, _ = UserParameters.objects.get_or_create(user=self.request.user)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    # HTMX: if ?partial=1, return the fragment only
    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/general_form.html"]
        return [self.template_name]
