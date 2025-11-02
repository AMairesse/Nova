# user_settings/views/general.py
from __future__ import annotations

from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import update_session_auth_hash
from django.contrib.messages.views import SuccessMessageMixin
from django.views.generic import UpdateView
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.shortcuts import render

from nova.models.UserObjects import UserParameters
from user_settings.forms import UserParametersForm
from user_settings.mixins import DashboardRedirectMixin


class GeneralSettingsView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
    SuccessMessageMixin,
    UpdateView
):
    """
    Simple *one-row* model; the row is auto-created if it does not exist.
    """
    model = UserParameters
    form_class = UserParametersForm
    template_name = "user_settings/general_form.html"
    success_message = "Settings saved successfully"
    dashboard_tab = "general"
    success_url = reverse_lazy("user_settings:dashboard")

    # Ensure every user has a row
    def get_object(self, queryset=None):
        obj, _ = UserParameters.objects.get_or_create(user=self.request.user)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'password_form' not in context:
            context['password_form'] = PasswordChangeForm(user=self.request.user)
        return context

    # HTMX: if ?partial=1, return only the fragment
    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/general_form.html"]
        return [self.template_name]

    def form_valid(self, form):
        redirect_response = super().form_valid(form)

        if self.request.headers.get("HX-Request") == "true":
            resp = HttpResponse(status=204)
            resp["HX-Refresh"] = "true"
            return resp

        return redirect_response

    def post(self, request, *args, **kwargs):
        # Check if this is a password change request
        if 'old_password' in request.POST:
            # Handle password change
            form = PasswordChangeForm(user=request.user, data=request.POST)
            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)  # Keep user logged in
                # Return success response for HTMX
                if request.headers.get("HX-Request") == "true":
                    return render(request, 'user_settings/fragments/password_change_success.html')
                # For non-HTMX, redirect to dashboard
                return self.get_success_url()
            else:
                # Return form with errors for HTMX
                if request.headers.get("HX-Request") == "true":
                    # Set up context manually for error case
                    obj = self.get_object()  # Ensure object is set
                    form_instance = self.get_form()
                    context = {
                        'form': form_instance,
                        'password_form': form,
                        'object': obj,
                        'view': self,
                    }
                    return render(request, 'user_settings/fragments/general_form.html', context)
                # For non-HTMX, fall back to normal form handling
        # Otherwise, handle normal form
        return super().post(request, *args, **kwargs)
