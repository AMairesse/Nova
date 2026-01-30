from asgiref.sync import async_to_sync
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import UpdateView

from nova.llm.embeddings import (
    compute_embedding,
    get_custom_http_provider,
    get_embeddings_provider,
)
from nova.models.UserObjects import UserParameters
from user_settings.forms import UserMemoryEmbeddingsForm
from user_settings.mixins import DashboardRedirectMixin


class MemorySettingsView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
    SuccessMessageMixin,
    UpdateView
):
    """
    Configure long-term memory embeddings.

    Memory content itself is not edited here (tool-driven memory).
    """
    model = UserParameters
    form_class = UserMemoryEmbeddingsForm
    template_name = "user_settings/memory_form.html"
    success_message = _("Memory settings updated successfully")
    dashboard_tab = "memory"
    success_url = reverse_lazy("user_settings:dashboard")

    # Ensure every user has a UserParameters row
    def get_object(self, queryset=None):
        obj, _ = UserParameters.objects.get_or_create(user=self.request.user)
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

    def post(self, request, *args, **kwargs):
        # Handle healthcheck button
        if request.POST.get("action") == "test_embeddings":
            # Prefer testing the values currently typed in the form (without saving).
            # If not present, fall back to the runtime provider selection.
            form = self.form_class(data=request.POST, instance=self.get_object())

            provider_override = None
            if form.is_valid() and form.cleaned_data.get("memory_embeddings_enabled"):
                provider_override = get_custom_http_provider(
                    base_url=form.cleaned_data.get("memory_embeddings_url"),
                    model=form.cleaned_data.get("memory_embeddings_model"),
                    api_key=form.cleaned_data.get("memory_embeddings_api_key"),
                )

            provider = provider_override or get_embeddings_provider()
            if not provider:
                messages.warning(request, _("Embeddings provider is not configured."))
                return self.get(request, *args, **kwargs)

            try:
                vec = async_to_sync(compute_embedding)(
                    "healthcheck",
                    provider_override=provider_override,
                )
                if vec is None:
                    messages.warning(request, _("Embeddings provider returned no vector (disabled)."))
                else:
                    messages.success(
                        request,
                        _("Embeddings OK: got %(dims)s dimensions from %(provider)s")
                        % {"dims": len(vec), "provider": provider.provider_type},
                    )
            except Exception as e:
                messages.error(request, _("Embeddings test failed: %(err)s") % {"err": str(e)})

            # The Memory settings form is typically posted via HTMX with
            # `hx-swap="none"`, so the response body is not rendered.
            # Force a full refresh so Django messages are visible immediately.
            if request.headers.get("HX-Request") == "true":
                resp = HttpResponse(status=204)
                resp["HX-Refresh"] = "true"
                return resp

            return self.get(request, *args, **kwargs)

        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        provider = get_embeddings_provider()
        context["embeddings_provider"] = provider
        context["help_text"] = _(
            "Configure semantic search (embeddings) for long-term memory. "
            "If no provider is configured, Nova will run in text-search (FTS) mode only."
        )
        return context
