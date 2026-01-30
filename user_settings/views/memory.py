from asgiref.sync import async_to_sync
import logging

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
from nova.models.Memory import MemoryItemEmbedding
from nova.tasks.memory_rebuild_tasks import rebuild_user_memory_embeddings_task
from nova.models.UserObjects import UserParameters
from user_settings.forms import UserMemoryEmbeddingsForm
from user_settings.mixins import DashboardRedirectMixin


logger = logging.getLogger(__name__)


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

    def get_initial(self):
        """When a rebuild confirmation is pending, pre-fill the form with the
        unsaved values stored in session.
        """

        initial = super().get_initial()
        pending = self.request.session.get("memory_embeddings_pending")
        if isinstance(pending, dict):
            initial.update(pending)
        return initial

    # HTMX: if ?partial=1, return only the fragment
    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/memory_form.html"]
        return [self.template_name]

    def form_valid(self, form):
        # NOTE: save is handled in post() so we can implement a two-step
        # confirmation flow when changing the embeddings provider/model.
        return super().form_valid(form)

    def post(self, request, *args, **kwargs):
        # ------------------------------------------------------------
        # Cancel pending confirmation
        # ------------------------------------------------------------
        if request.POST.get("action") == "cancel_reembed":
            request.session.pop("memory_embeddings_pending", None)
            messages.info(request, _("Embeddings settings change cancelled."))
            if request.headers.get("HX-Request") == "true":
                resp = HttpResponse(status=204)
                resp["HX-Refresh"] = "true"
                return resp
            return self.get(request, *args, **kwargs)

        # ------------------------------------------------------------
        # Confirm pending confirmation
        # ------------------------------------------------------------
        if request.POST.get("action") == "confirm_reembed":
            pending = request.session.get("memory_embeddings_pending")
            if not isinstance(pending, dict):
                messages.warning(request, _("No pending embeddings change to confirm."))
                if request.headers.get("HX-Request") == "true":
                    resp = HttpResponse(status=204)
                    resp["HX-Refresh"] = "true"
                    return resp
                return self.get(request, *args, **kwargs)

            obj = self.get_object()
            for k, v in pending.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            obj.save(update_fields=[
                "memory_embeddings_enabled",
                "memory_embeddings_url",
                "memory_embeddings_model",
                "memory_embeddings_api_key",
            ])
            request.session.pop("memory_embeddings_pending", None)

            # Kick-off rebuild in the background (best-effort).
            rebuild_user_memory_embeddings_task.delay(request.user.id)
            messages.success(
                request,
                _("Embeddings settings updated. Rebuilding embeddings in background."),
            )

            if request.headers.get("HX-Request") == "true":
                resp = HttpResponse(status=204)
                resp["HX-Refresh"] = "true"
                return resp
            return self.get(request, *args, **kwargs)

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

        # ------------------------------------------------------------
        # Normal save (with optional confirmation)
        # ------------------------------------------------------------
        obj = self.get_object()

        # IMPORTANT:
        # `ModelForm.is_valid()` mutates `instance` (via `construct_instance`).
        # So we must snapshot the "old" values *before* calling `is_valid()`,
        # otherwise old==new and change detection never triggers.
        old_signature = (
            bool(obj.memory_embeddings_enabled),
            (obj.memory_embeddings_url or "").strip(),
            (obj.memory_embeddings_model or "").strip(),
        )

        form = self.form_class(data=request.POST, instance=obj)
        if not form.is_valid():
            # Render errors in-place (HTMX will refresh the whole tab anyway).
            return self.form_invalid(form)

        new_data = form.cleaned_data

        # Detect a provider/model change that should trigger a rebuild.
        new_signature = (
            bool(new_data.get("memory_embeddings_enabled")),
            (new_data.get("memory_embeddings_url") or "").strip(),
            (new_data.get("memory_embeddings_model") or "").strip(),
        )

        signature_changed = old_signature != new_signature
        will_have_embeddings = bool(new_signature[0] and new_signature[1])

        logger.info(
            "[memory.settings.save] user=%s hx=%s action=%s old=%s new=%s changed=%s will_have=%s post_keys=%s",
            request.user.id,
            request.headers.get("HX-Request"),
            request.POST.get("action"),
            old_signature,
            new_signature,
            signature_changed,
            will_have_embeddings,
            sorted(list(request.POST.keys())),
        )

        # If changing to a different provider/model while embeddings are enabled,
        # require confirmation because existing vectors become inconsistent.
        if signature_changed and will_have_embeddings and request.POST.get("confirm") != "1":
            count = MemoryItemEmbedding.objects.filter(user=request.user).count()
            request.session["memory_embeddings_pending"] = {
                "memory_embeddings_enabled": new_data.get("memory_embeddings_enabled"),
                "memory_embeddings_url": new_data.get("memory_embeddings_url"),
                "memory_embeddings_model": new_data.get("memory_embeddings_model"),
                "memory_embeddings_api_key": new_data.get("memory_embeddings_api_key"),
            }
            messages.warning(
                request,
                _(
                    "Changing the embeddings provider/model will trigger a rebuild of %(count)s embeddings. "
                    "Click Confirm to proceed or Cancel to abort."
                )
                % {"count": count},
            )

            if request.headers.get("HX-Request") == "true":
                resp = HttpResponse(status=204)
                resp["HX-Refresh"] = "true"
                return resp
            return self.get(request, *args, **kwargs)

        # No confirmation needed → save immediately.
        self.object = obj
        form.save()
        messages.success(request, self.success_message)

        if request.headers.get("HX-Request") == "true":
            resp = HttpResponse(status=204)
            resp["HX-Refresh"] = "true"
            return resp
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending = self.request.session.get("memory_embeddings_pending")
        context["has_pending_reembed"] = isinstance(pending, dict)
        if context["has_pending_reembed"]:
            context["pending_reembed_count"] = MemoryItemEmbedding.objects.filter(
                user=self.request.user
            ).count()
        provider = get_embeddings_provider()
        context["embeddings_provider"] = provider
        context["help_text"] = _(
            "Configure semantic search (embeddings) for long-term memory. "
            "If no provider is configured, Nova will run in text-search (FTS) mode only."
        )
        return context
