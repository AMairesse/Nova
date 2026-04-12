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
    resolve_embeddings_provider_for_values,
)
from nova.memory.service import count_memory_chunk_embeddings
from nova.models.AgentConfig import AgentConfig
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters
from nova.tasks.conversation_embedding_tasks import rebuild_user_conversation_embeddings_task
from nova.tasks.memory_rebuild_tasks import rebuild_user_memory_embeddings_task
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

    def get_object(self, queryset=None):
        obj, _ = UserParameters.objects.get_or_create(user=self.request.user)
        return obj

    def get_initial(self):
        initial = super().get_initial()
        pending = self.request.session.get("memory_embeddings_pending")
        if isinstance(pending, dict):
            initial.update(pending)
        return initial

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/memory_form.html"]
        return [self.template_name]

    def form_valid(self, form):
        return super().form_valid(form)

    def _normalize_source(self, value: str | None) -> str:
        if value in set(MemoryEmbeddingsSource.values):
            return str(value)
        return MemoryEmbeddingsSource.SYSTEM

    def _instance_values(self, obj: UserParameters) -> dict:
        return {
            "memory_embeddings_source": self._normalize_source(obj.memory_embeddings_source),
            "memory_embeddings_url": (obj.memory_embeddings_url or "").strip(),
            "memory_embeddings_model": (obj.memory_embeddings_model or "").strip(),
            "memory_embeddings_api_key": obj.memory_embeddings_api_key or None,
        }

    def _cleaned_values(self, cleaned_data: dict) -> dict:
        return {
            "memory_embeddings_source": self._normalize_source(
                cleaned_data.get("memory_embeddings_source")
            ),
            "memory_embeddings_url": (cleaned_data.get("memory_embeddings_url") or "").strip(),
            "memory_embeddings_model": (cleaned_data.get("memory_embeddings_model") or "").strip(),
            "memory_embeddings_api_key": cleaned_data.get("memory_embeddings_api_key") or None,
        }

    def _form_display_values(self, form) -> dict:
        if form.is_bound:
            api_key = form.data.get("memory_embeddings_api_key")
            if api_key in ("", None) and getattr(form.instance, "pk", None):
                api_key = form.instance.memory_embeddings_api_key or None
            return {
                "memory_embeddings_source": self._normalize_source(
                    form.data.get("memory_embeddings_source")
                ),
                "memory_embeddings_url": (form.data.get("memory_embeddings_url") or "").strip(),
                "memory_embeddings_model": (form.data.get("memory_embeddings_model") or "").strip(),
                "memory_embeddings_api_key": api_key,
            }

        initial = getattr(form, "initial", {}) or {}
        instance = getattr(form, "instance", None)
        return {
            "memory_embeddings_source": self._normalize_source(
                initial.get(
                    "memory_embeddings_source",
                    getattr(instance, "memory_embeddings_source", MemoryEmbeddingsSource.SYSTEM),
                )
            ),
            "memory_embeddings_url": (
                initial.get("memory_embeddings_url", getattr(instance, "memory_embeddings_url", "")) or ""
            ).strip(),
            "memory_embeddings_model": (
                initial.get("memory_embeddings_model", getattr(instance, "memory_embeddings_model", "")) or ""
            ).strip(),
            "memory_embeddings_api_key": initial.get(
                "memory_embeddings_api_key",
                getattr(instance, "memory_embeddings_api_key", None),
            ),
        }

    def _resolve_values(self, values: dict):
        return resolve_embeddings_provider_for_values(
            selected_source=values.get("memory_embeddings_source"),
            base_url=values.get("memory_embeddings_url"),
            model=values.get("memory_embeddings_model"),
            api_key=values.get("memory_embeddings_api_key"),
        )

    def _render_refresh_or_get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") == "true":
            resp = HttpResponse(status=204)
            resp["HX-Refresh"] = "true"
            return resp
        return self.get(request, *args, **kwargs)

    def _warning_for_missing_provider(self, selected_source: str) -> str:
        if selected_source == MemoryEmbeddingsSource.SYSTEM:
            return _(
                "No system embeddings provider is configured. Semantic search will stay inactive until one is added."
            )
        if selected_source == MemoryEmbeddingsSource.CUSTOM:
            return _(
                "Custom embeddings provider is not configured yet. Provide an endpoint URL to enable semantic search."
            )
        return _("Embeddings are disabled for memory.")

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "cancel_reembed":
            request.session.pop("memory_embeddings_pending", None)
            messages.info(request, _("Embeddings settings change cancelled."))
            return self._render_refresh_or_get(request, *args, **kwargs)

        if request.POST.get("action") == "confirm_reembed":
            pending = request.session.get("memory_embeddings_pending")
            if not isinstance(pending, dict):
                messages.warning(request, _("No pending embeddings change to confirm."))
                return self._render_refresh_or_get(request, *args, **kwargs)

            obj = self.get_object()
            for key, value in pending.items():
                if hasattr(obj, key):
                    setattr(obj, key, value)
            obj.save(
                update_fields=[
                    "memory_embeddings_source",
                    "memory_embeddings_url",
                    "memory_embeddings_model",
                    "memory_embeddings_api_key",
                ]
            )
            request.session.pop("memory_embeddings_pending", None)

            rebuild_user_memory_embeddings_task.delay(request.user.id)
            rebuild_user_conversation_embeddings_task.delay(request.user.id)
            messages.success(
                request,
                _("Embeddings settings updated. Rebuilding embeddings in background."),
            )
            return self._render_refresh_or_get(request, *args, **kwargs)

        obj = self.get_object()

        if request.POST.get("action") == "test_embeddings":
            form = self.form_class(data=request.POST, instance=obj)
            if not form.is_valid():
                self.object = obj
                return self.form_invalid(form)

            values = self._cleaned_values(form.cleaned_data)
            selected_source = values["memory_embeddings_source"]

            if selected_source == MemoryEmbeddingsSource.CUSTOM:
                provider = get_custom_http_provider(
                    base_url=values["memory_embeddings_url"],
                    model=values["memory_embeddings_model"],
                    api_key=values["memory_embeddings_api_key"],
                )
            else:
                provider = self._resolve_values(values).provider

            if not provider:
                messages.warning(request, self._warning_for_missing_provider(selected_source))
                return self._render_refresh_or_get(request, *args, **kwargs)

            try:
                vec = async_to_sync(compute_embedding)(
                    "healthcheck",
                    provider_override=provider,
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

            return self._render_refresh_or_get(request, *args, **kwargs)

        old_values = self._instance_values(obj)
        old_resolved = self._resolve_values(old_values)

        form = self.form_class(data=request.POST, instance=obj)
        if not form.is_valid():
            self.object = obj
            return self.form_invalid(form)

        new_values = self._cleaned_values(form.cleaned_data)
        new_resolved = self._resolve_values(new_values)

        signature_changed = old_resolved.signature != new_resolved.signature
        requires_confirmation = (
            signature_changed
            and old_resolved.signature is not None
            and new_resolved.signature is not None
            and request.POST.get("confirm") != "1"
        )
        should_queue_rebuild = signature_changed and new_resolved.signature is not None

        if requires_confirmation:
            count = async_to_sync(count_memory_chunk_embeddings)(user=request.user)
            request.session["memory_embeddings_pending"] = {
                "memory_embeddings_source": new_values.get("memory_embeddings_source"),
                "memory_embeddings_url": new_values.get("memory_embeddings_url"),
                "memory_embeddings_model": new_values.get("memory_embeddings_model"),
                "memory_embeddings_api_key": new_values.get("memory_embeddings_api_key"),
            }
            messages.warning(
                request,
                _(
                    "Changing the active embeddings provider will trigger a rebuild of %(count)s embeddings. "
                    "Click Confirm to proceed or Cancel to abort."
                )
                % {"count": count},
            )
            return self._render_refresh_or_get(request, *args, **kwargs)

        self.object = obj
        form.save()

        if should_queue_rebuild:
            rebuild_user_memory_embeddings_task.delay(request.user.id)
            rebuild_user_conversation_embeddings_task.delay(request.user.id)
            messages.success(
                request,
                _("Embeddings settings updated. Rebuilding embeddings in background."),
            )
        else:
            messages.success(request, self.success_message)

        return self._render_refresh_or_get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending = self.request.session.get("memory_embeddings_pending")
        context["has_pending_reembed"] = isinstance(pending, dict)
        if context["has_pending_reembed"]:
            context["pending_reembed_count"] = async_to_sync(
                count_memory_chunk_embeddings
            )(user=self.request.user)

        form = context.get("form")
        display_values = self._form_display_values(form)
        resolved = self._resolve_values(display_values)

        selected_source = display_values["memory_embeddings_source"]
        context["selected_embeddings_source"] = selected_source
        context["resolved_embeddings_provider"] = resolved
        context["embeddings_provider"] = resolved.provider
        context["embeddings_provider_source"] = resolved.provider_source
        context["system_embeddings_provider"] = resolved.system_provider
        context["system_embeddings_provider_available"] = resolved.system_provider_available
        context["show_system_embeddings_warning"] = (
            selected_source == MemoryEmbeddingsSource.SYSTEM and not resolved.system_provider_available
        )
        context["show_custom_embeddings_warning"] = (
            selected_source == MemoryEmbeddingsSource.CUSTOM and resolved.provider is None
        )
        memory_enabled_agent_count = (
            AgentConfig.objects.filter(user=self.request.user, tools__tool_subtype="memory")
            .distinct()
            .count()
        )
        context["memory_enabled_agent_count"] = memory_enabled_agent_count
        context["show_memory_usage_info"] = memory_enabled_agent_count == 0
        context["help_text"] = _(
            "Memory is user-scoped and shared across the agents that have the Memory capability enabled. "
            "Configure semantic retrieval here, while lexical search over visible memory files remains available either way."
        )
        return context
