# user_settings/views/provider.py
from __future__ import annotations

import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET
from django.views.generic import ListView

from nova.models.Provider import LLMProvider, check_and_create_system_provider
from nova.providers import get_provider_defaults_map
from nova.tasks.provider_validation_tasks import validate_provider_configuration_task
from user_settings.forms import LLMProviderForm
from user_settings.mixins import (
    OwnerCreateView,
    OwnerUpdateView,
    OwnerDeleteView,
    DashboardRedirectMixin,
    SystemReadonlyMixin,
)


# ---------------------------------------------------------------------------#
#  List                                                                      #
# ---------------------------------------------------------------------------#
class ProviderListView(LoginRequiredMixin, ListView):
    model = LLMProvider
    template_name = "user_settings/provider_list.html"
    context_object_name = "providers"
    paginate_by = 5

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/provider_table.html"]
        return super().get_template_names()

    def get_queryset(self):
        # Ensure the system provider exists
        check_and_create_system_provider()
        # Return the user's providers and the system's one
        return LLMProvider.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by('user', 'name')


class ProviderValidationActionMixin:
    test_action_name = "test_provider"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        provider = getattr(self, "object", None) or getattr(context.get("form"), "instance", None)
        context["provider_instance"] = provider
        context["provider_defaults_map"] = get_provider_defaults_map()
        if provider and provider.pk:
            context["provider_validation_status_url"] = reverse(
                "user_settings:provider-validation-status",
                args=[provider.pk],
            )
        return context

    def _build_edit_url(self, provider: LLMProvider) -> str:
        url = reverse("user_settings:provider-edit", args=[provider.pk])
        origin = self.request.POST.get("from") or self.request.GET.get("from")
        if not origin:
            return url
        return f"{url}?{urlencode({'from': origin})}"

    def _handle_provider_validation_action(self):
        self.object = self.get_object() if "pk" in self.kwargs else None
        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)

        provider = form.save(commit=False)
        if not provider.user_id:
            provider.user = self.request.user
        provider.save()

        previous_state = {
            "validation_status": provider.validation_status,
            "validation_summary": provider.validation_summary,
            "validation_capabilities": provider.validation_capabilities,
            "validated_at": provider.validated_at,
            "validated_fingerprint": provider.validated_fingerprint,
            "validation_task_id": provider.validation_task_id,
            "validation_requested_fingerprint": provider.validation_requested_fingerprint,
        }
        requested_fingerprint = provider.compute_validation_fingerprint()
        task_id = uuid.uuid4().hex

        provider.mark_validation_started(
            task_id=task_id,
            requested_fingerprint=requested_fingerprint,
        )

        try:
            validate_provider_configuration_task.apply_async(
                args=[provider.pk, requested_fingerprint],
                task_id=task_id,
            )
        except Exception as exc:
            for field_name, value in previous_state.items():
                setattr(provider, field_name, value)
            provider.save(
                update_fields=[
                    "validation_status",
                    "validation_summary",
                    "validation_capabilities",
                    "validated_at",
                    "validated_fingerprint",
                    "validation_task_id",
                    "validation_requested_fingerprint",
                    "updated_at",
                ]
            )
            messages.error(
                self.request,
                _("Provider validation could not be started: %(error)s")
                % {"error": str(exc)},
            )
            return redirect(self._build_edit_url(provider))

        messages.info(
            self.request,
            _("Provider validation started in background. You can leave this page."),
        )
        return redirect(self._build_edit_url(provider))

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == self.test_action_name:
            return self._handle_provider_validation_action()
        return super().post(request, *args, **kwargs)


# ---------------------------------------------------------------------------#
#  CRUD                                                                      #
# ---------------------------------------------------------------------------#
class ProviderCreateView(
    ProviderValidationActionMixin, DashboardRedirectMixin, LoginRequiredMixin, OwnerCreateView
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderUpdateView(  # type: ignore[misc]
    ProviderValidationActionMixin,
    DashboardRedirectMixin,
    LoginRequiredMixin,
    OwnerUpdateView,
    SystemReadonlyMixin,
):
    model = LLMProvider
    form_class = LLMProviderForm
    template_name = "user_settings/provider_form.html"
    dashboard_tab = "providers"


class ProviderDeleteView(  # type: ignore[misc]
    LoginRequiredMixin, SystemReadonlyMixin, DashboardRedirectMixin, OwnerDeleteView
):
    model = LLMProvider
    template_name = "user_settings/provider_confirm_delete.html"
    dashboard_tab = "providers"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


@login_required
@require_GET
def provider_validation_status(request, pk: int):
    provider = get_object_or_404(LLMProvider, pk=pk, user=request.user)
    return JsonResponse(provider.build_validation_status_payload())
