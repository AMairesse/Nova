# user_settings/views/tool.py
from __future__ import annotations

import logging
import json
from collections import OrderedDict
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Count
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DeleteView, FormView, TemplateView
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Fieldset, Field
from asgiref.sync import async_to_sync, sync_to_async

from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    SystemReadonlyMixin,
    OwnerAccessMixin,
    SecretPreserveMixin,
    SuccessMessageMixin,
    DashboardRedirectMixin,
)
from user_settings.forms import APIToolOperationForm, ToolForm, ToolCredentialForm
from nova.models.APIToolOperation import APIToolOperation
from nova.models.Tool import Tool, ToolCredential
from nova.plugins import get_plugin_for_builtin_subtype
from nova.plugins.catalog import (
    build_tools_page_catalog,
    ensure_capability_tooling,
    get_tool_connection_status,
    get_user_creatable_plugins,
    resolve_connection_kind,
)
from nova.plugins.builtins import get_metadata, get_tool_type
from nova.mcp.client import MCPClient
from nova.mcp import oauth_service as mcp_oauth_service

logger = logging.getLogger(__name__)


def _first_form_error(form: forms.Form) -> str:
    for errors in form.errors.values():
        if errors:
            return str(errors[0])
    return "Invalid configuration."


def _build_mcp_oauth_context(credential: ToolCredential | None) -> dict | None:
    if credential is None:
        return None
    oauth_config = {}
    if isinstance(credential.config, dict):
        oauth_config = credential.config.get("mcp_oauth") or {}
    if not isinstance(oauth_config, dict):
        oauth_config = {}

    status = str(oauth_config.get("status") or "").strip().lower()
    last_error = str(oauth_config.get("last_error") or "").strip()
    auth_type = str(credential.auth_type or "").strip().lower()
    is_connected = auth_type == "oauth_managed" and status == "connected"
    needs_reconnect = auth_type == "oauth_managed" and status == "reconnect_required"

    if is_connected:
        badge_class = "text-bg-success"
        status_label = "Connected"
        action_label = "Reconnect with OAuth"
    elif needs_reconnect:
        badge_class = "text-bg-warning"
        status_label = "Reconnect required"
        action_label = "Reconnect with OAuth"
    else:
        badge_class = "text-bg-secondary"
        status_label = "Not connected"
        action_label = "Connect with OAuth"

    return {
        "is_connected": is_connected,
        "needs_reconnect": needs_reconnect,
        "can_verify": bool(credential.access_token or credential.refresh_token),
        "status_label": status_label,
        "badge_class": badge_class,
        "action_label": action_label,
        "last_error": last_error,
        "using_managed_oauth": auth_type == "oauth_managed",
        "has_advanced_credentials": bool(credential.client_id or credential.client_secret),
    }


def _get_builtin_metadata_for_tool(tool: Tool) -> dict:
    metadata = get_tool_type(tool.tool_subtype or "")
    if metadata:
        return metadata
    return get_metadata(tool.python_path)


class ToolListView(LoginRequiredMixin, UserOwnedQuerySetMixin, ListView):
    model = Tool
    template_name = "user_settings/tool_list.html"
    context_object_name = "tools"
    paginate_by = 20
    ordering = ["name"]

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/tool_table.html"]
        return super().get_template_names()

    def get_queryset(self):
        ensure_capability_tooling()

        # Limit tools to current user + system tools
        base_qs = Tool.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        )

        # Annotate how many of THIS user's agents use each tool.
        #
        # For user-owned tools: count all related agents (they already belong to this user).
        # For system tools: count only agents of the current user.
        return base_qs.annotate(
            agent_count=Count(
                "agents",
                filter=Q(agents__user=self.request.user),
                distinct=True,
            )
        ).prefetch_related("credentials").order_by("user", "name", "id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tool_catalog"] = build_tools_page_catalog(
            self.request.user,
            tools=list(context["tools"]),
        )
        return context


# ---------------------------------------------------------------------------#
#  CREATE / UPDATE (unified settings screen)                                 #
# ---------------------------------------------------------------------------#
def _tool_redirect_url(name: str, *, pk: int, origin: str = "") -> str:
    base_url = reverse(name, args=[pk])
    if not origin:
        return base_url
    return f"{base_url}?{urlencode({'from': origin})}"


def _tool_kind_sections() -> list[dict]:
    backend_ids = {"search", "python"}
    sections = [
        {"title": _("Capabilities with backends"), "items": []},
        {"title": _("Connections"), "items": []},
    ]
    for plugin in get_user_creatable_plugins():
        item = {
            "kind": plugin.plugin_id,
            "label": plugin.add_label or plugin.label,
            "description": (plugin.settings_metadata or {}).get("description", ""),
        }
        if plugin.plugin_id in backend_ids:
            sections[0]["items"].append(item)
        else:
            sections[1]["items"].append(item)
    return [section for section in sections if section["items"]]


def _tool_mapping_from_instance(tool: Tool) -> dict[str, str]:
    return {
        "tool_type": tool.tool_type,
        "tool_subtype": tool.tool_subtype or "",
    }


class _ToolSettingsBaseView(DashboardRedirectMixin, LoginRequiredMixin, TemplateView):
    model = Tool
    template_name = "user_settings/tool_form.html"
    dashboard_tab = "tools"
    object: Tool | None = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.object = self.get_tool_object()
        return super().dispatch(request, *args, **kwargs)

    def get_tool_object(self) -> Tool | None:
        return None

    def get(self, request, *args, **kwargs):
        return self._render()

    def post(self, request, *args, **kwargs):
        return self._handle_submit()

    def _origin(self) -> str:
        return str(self.request.POST.get("from") or self.request.GET.get("from") or "").strip()

    def _tool_settings_url(self, tool: Tool) -> str:
        return _tool_redirect_url(
            "user_settings:tool-edit",
            pk=tool.pk,
            origin=self._origin(),
        )

    def _selected_connection_kind(self) -> str:
        if self.object is not None:
            if self.object.tool_type == Tool.ToolType.BUILTIN:
                plugin = get_plugin_for_builtin_subtype(self.object.tool_subtype or "")
                return plugin.plugin_id if plugin is not None else ""
            return str(self.object.tool_type or "")
        return str(
            self.request.POST.get("connection_kind")
            or self.request.GET.get("kind")
            or ""
        ).strip()

    def _selected_mapping(self) -> dict[str, str] | None:
        if self.object is not None:
            return _tool_mapping_from_instance(self.object)
        selected_kind = self._selected_connection_kind()
        if not selected_kind:
            return None
        mapping = resolve_connection_kind(selected_kind)
        if mapping is None:
            return None
        return {
            "tool_type": mapping["tool_type"],
            "tool_subtype": mapping["tool_subtype"],
        }

    def _build_tool_draft(self, mapping: dict[str, str] | None) -> Tool | None:
        if mapping is None:
            return None
        tool = Tool(
            user=self.request.user,
            tool_type=mapping["tool_type"],
            tool_subtype=mapping["tool_subtype"],
        )
        if tool.tool_type != Tool.ToolType.MCP:
            tool.transport_type = Tool.TransportType.STREAMABLE_HTTP
        return tool

    def _build_tool_form(self, *, bind: bool) -> ToolForm | None:
        if self.object is None and not self._selected_connection_kind():
            return None
        data = self.request.POST if bind else None
        return ToolForm(
            data=data,
            instance=self.object or Tool(),
            user=self.request.user,
            fixed_connection_kind=self._selected_connection_kind(),
        )

    def _config_credential(self, tool: Tool) -> ToolCredential | None:
        if not tool.pk:
            return None
        return ToolCredential.objects.filter(
            user=self.request.user,
            tool=tool,
        ).first()

    def _build_settings_form(self, *, bind: bool, tool: Tool | None):
        if tool is None:
            return None
        data = self.request.POST if bind else None
        if tool.tool_type == Tool.ToolType.BUILTIN:
            meta = _get_builtin_metadata_for_tool(tool)
            credential = self._config_credential(tool)
            initial = dict(credential.config or {}) if credential else {}
            return _BuiltInConfigForm(
                data=data,
                meta=meta,
                tool=tool,
                user=self.request.user,
                initial=initial,
            )

        credential = self._config_credential(tool)
        if credential is None:
            credential = ToolCredential(
                user=self.request.user,
                tool=tool,
                auth_type="none",
            )
        return ToolCredentialForm(
            data=data,
            instance=credential,
            tool=tool,
            user=self.request.user,
        )

    def _save_settings_form(self, *, form, tool: Tool) -> ToolCredential | None:
        if tool.tool_type == Tool.ToolType.BUILTIN:
            credential, _ = ToolCredential.objects.get_or_create(
                user=self.request.user,
                tool=tool,
                defaults={"auth_type": "basic"},
            )
            credential.config.update(form.cleaned_data)
            credential.save()
            return credential

        credential = form.save(commit=False)
        credential.user = self.request.user
        credential.tool = tool
        credential.save()
        return credential

    def _kind_label(self, tool: Tool | None) -> str:
        if tool is None:
            return ""
        if tool.tool_type == Tool.ToolType.BUILTIN:
            plugin = get_plugin_for_builtin_subtype(tool.tool_subtype or "")
            if plugin is not None:
                return plugin.add_label or plugin.label
        return tool.get_tool_type_display()

    def _render(self, *, status: int = 200, tool_form=None, settings_form=None):
        selected_mapping = self._selected_mapping()
        draft_tool = self.object or self._build_tool_draft(selected_mapping)
        tool_form = tool_form or self._build_tool_form(bind=False)
        settings_form = settings_form or self._build_settings_form(bind=False, tool=draft_tool)
        current_tool = self.object or draft_tool
        current_credential = self._config_credential(self.object) if self.object else None
        selected_connection_mode = ""
        if settings_form is not None and "connection_mode" in getattr(settings_form, "fields", {}):
            selected_connection_mode = str(
                settings_form.data.get("connection_mode")
                or settings_form.initial.get("connection_mode")
                or ""
            ).strip()
        metadata = _get_builtin_metadata_for_tool(current_tool) if current_tool and current_tool.tool_type == Tool.ToolType.BUILTIN else {}
        can_test_connection = bool(
            self.object
            and current_tool
            and (
                (current_tool.tool_type == Tool.ToolType.BUILTIN and metadata.get("requires_config"))
                or current_tool.tool_type == Tool.ToolType.MCP
            )
        )

        context = {
            "tool": current_tool,
            "object": self.object,
            "tool_form": tool_form,
            "settings_form": settings_form,
            "form": tool_form or ToolForm(user=self.request.user),
            "show_kind_chooser": self.object is None and not self._selected_connection_kind(),
            "connection_kind_sections": _tool_kind_sections(),
            "page_title": _("Settings") if self.object else _("Add connection"),
            "connection_kind_label": self._kind_label(current_tool),
            "connection_status": get_tool_connection_status(self.object) if self.object else None,
            "can_test_connection": can_test_connection,
            "selected_connection_mode": selected_connection_mode,
            "dashboard_tab": self.dashboard_tab,
        }
        if metadata:
            context["metadata"] = metadata
        if settings_form is not None and hasattr(settings_form, "connection_mode_definitions"):
            context["connection_modes"] = list(settings_form.connection_mode_definitions)
        if current_tool and current_tool.tool_type == Tool.ToolType.MCP:
            context["mcp_oauth"] = _build_mcp_oauth_context(current_credential)
        if self.object and self.object.tool_type == Tool.ToolType.API:
            context["api_operations"] = list(
                APIToolOperation.objects.filter(tool=self.object).order_by("name", "id")
            )
        return self.render_to_response(context, status=status)

    def _handle_submit(self):
        tool_form = self._build_tool_form(bind=True)
        if tool_form is None:
            messages.error(self.request, _("Choose a connection type to continue."))
            return self._render(status=400)

        selected_mapping = self._selected_mapping()
        draft_tool = self.object or self._build_tool_draft(selected_mapping)
        settings_form = self._build_settings_form(bind=True, tool=draft_tool)

        tool_valid = tool_form.is_valid()
        settings_valid = settings_form.is_valid() if settings_form is not None else True
        if not (tool_valid and settings_valid):
            return self._render(
                status=400,
                tool_form=tool_form,
                settings_form=settings_form,
            )

        is_new = self.object is None
        tool = tool_form.save(commit=False)
        if is_new:
            tool.user = self.request.user
        tool.save()
        if settings_form is not None:
            self._save_settings_form(form=settings_form, tool=tool)

        self.object = tool
        messages.success(self.request, _("Connection saved."))
        return HttpResponseRedirect(self._tool_settings_url(tool))


class ToolCreateView(_ToolSettingsBaseView):
    pass


class ToolUpdateView(_ToolSettingsBaseView):
    def get_tool_object(self) -> Tool | None:
        return get_object_or_404(Tool, pk=self.kwargs["pk"], user=self.request.user)


class ToolDeleteView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
    SystemReadonlyMixin,
    OwnerAccessMixin,
    SuccessMessageMixin,
    DeleteView
):
    model = Tool
    template_name = "user_settings/tool_confirm_delete.html"
    dashboard_tab = "tools"


# ---------------------------------------------------------------------------#
#  Configure view                                                            #
# ---------------------------------------------------------------------------#
class _BuiltInConfigForm(SecretPreserveMixin, forms.Form):
    """Dynamic form for built-in tools exposing *config_fields* metadata."""
    secret_fields = (
        "password",
        "app_password",
        "token",
        "client_secret",
        "refresh_token",
        "access_token",
    )

    def __init__(self, *args, meta: dict, initial=None, **kw):
        # Store existing secrets
        self.user = kw.pop("user", None)
        self.tool = kw.pop("tool", None)
        self._existing_secrets = {k: v for k, v in (initial or {}).items()
                                  if k in self.secret_fields}

        super().__init__(*args, initial=initial or {}, **kw)

        # Crispy: no nested <form>
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        config_fields = meta.get("config_fields", [])

        # Build dynamic fields
        for cfg in config_fields:
            ftype = cfg["type"]
            required = False
            name = cfg["name"]
            label = cfg["label"]
            default = cfg.get("default")
            group = cfg.get("group")
            visible_if = cfg.get("visible_if")

            if ftype == "password":
                widget = forms.PasswordInput(render_value=False)
                self.fields[name] = forms.CharField(
                    label=label, required=required, widget=widget
                )
            elif ftype == "url":
                self.fields[name] = forms.URLField(label=label, required=required)
            elif ftype == "boolean":
                self.fields[name] = forms.BooleanField(
                    label=label, required=required, initial=default
                )
            elif ftype == "integer":
                widget = forms.NumberInput()
                self.fields[name] = forms.IntegerField(
                    label=label, required=required, widget=widget, initial=default
                )
            else:  # default to text
                self.fields[name] = forms.CharField(
                    label=label, required=required, initial=default
                )

            # Add group information for template rendering
            if group:
                self.fields[name].widget.attrs["data-group"] = group

            # Generic conditional visibility support.
            # We attach the condition to the *input element*; JS will hide the nearest wrapper.
            # Expected shape: visible_if: {"field": "enable_sending", "equals": true}
            if (
                isinstance(visible_if, dict)
                and visible_if.get("field")
                and ("equals" in visible_if)
            ):
                self.fields[name].widget.attrs["data-visible-if-field"] = str(
                    visible_if["field"]
                )
                # Use JSON to preserve booleans / numbers (JS will parse).
                self.fields[name].widget.attrs["data-visible-if-equals"] = json.dumps(
                    visible_if.get("equals")
                )

        # Preserve existing secrets
        keep_msg = _("Secret exists, leave blank to keep")
        for f in self.secret_fields:
            if f in self.fields and f in self._existing_secrets:
                fld = self.fields[f]
                fld.required = False
                fld.widget.attrs.setdefault("placeholder", keep_msg)

        # ------------------------------------------------------------------
        # Crispy layout: group fields server-side to avoid fragile DOM moves.
        # ------------------------------------------------------------------
        grouped: "OrderedDict[str, list[str]]" = OrderedDict()
        ungrouped_key = _("General")
        for cfg in config_fields:
            group_name = cfg.get("group") or ungrouped_key
            grouped.setdefault(group_name, []).append(cfg["name"])

        fieldsets = []
        for group_name, field_names in grouped.items():
            # Always render a Fieldset; keeps structure consistent.
            # Title: use translated "General" for the ungrouped bucket.
            title = group_name
            if group_name != ungrouped_key:
                # Basic prettifying for plain identifiers like "imap" -> "Imap".
                pretty_group = str(group_name).replace("_", " ").strip()
                acronyms = {"imap": "IMAP", "smtp": "SMTP"}
                title = acronyms.get(pretty_group.lower(), pretty_group.title())
            fieldsets.append(
                Fieldset(
                    title,
                    *[
                        Div(
                            Field(fname),
                            css_class="mb-3",
                        )
                        for fname in field_names
                        if fname in self.fields
                    ],
                    css_class="mb-4",
                )
            )

        self.helper.layout = Layout(*fieldsets)

    def clean(self):
        cleaned = super().clean()
        for secret_name in self.secret_fields:
            if cleaned.get(secret_name) in ("", None) and secret_name in self._existing_secrets:
                cleaned[secret_name] = self._existing_secrets[secret_name]

        if not self.tool or not self.user:
            return cleaned

        if self.tool.tool_subtype == "searxng":
            duplicate_qs = ToolCredential.objects.filter(
                user=self.user,
                tool__user=self.user,
                tool__tool_type=Tool.ToolType.BUILTIN,
                tool__tool_subtype="searxng",
                config__searxng_url=cleaned.get("searxng_url"),
                config__num_results=cleaned.get("num_results"),
            )
            if self.tool.pk:
                duplicate_qs = duplicate_qs.exclude(tool_id=self.tool.pk)
            if cleaned.get("searxng_url") and duplicate_qs.exists():
                raise forms.ValidationError(
                    _("A search backend with the same server URL and result limit already exists.")
                )

        return cleaned

# Compatibility route: keep old configure URLs working while the canonical
# screen is now the unified settings page.
class ToolConfigureView(LoginRequiredMixin, DashboardRedirectMixin, TemplateView):
    dashboard_tab = "tools"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        tool = get_object_or_404(Tool, pk=kwargs["pk"], user=request.user)
        return HttpResponseRedirect(
            _tool_redirect_url(
                "user_settings:tool-edit",
                pk=tool.pk,
                origin=str(request.GET.get("from") or request.POST.get("from") or "").strip(),
            )
        )


class _APIToolOperationViewBase(DashboardRedirectMixin, LoginRequiredMixin):
    dashboard_tab = "tools"
    template_name = "user_settings/api_tool_operation_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.tool = Tool.objects.get(
            pk=kwargs["tool_pk"],
            user=request.user,
            tool_type=Tool.ToolType.API,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("user_settings:tool-edit", args=[self.tool.pk])


class APIToolOperationCreateView(_APIToolOperationViewBase, FormView):
    form_class = APIToolOperationForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tool"] = self.tool
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "API operation created.")
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tool"] = self.tool
        ctx["operation"] = None
        return ctx


class APIToolOperationUpdateView(_APIToolOperationViewBase, FormView):
    form_class = APIToolOperationForm

    def dispatch(self, request, *args, **kwargs):
        self.tool = Tool.objects.get(
            pk=kwargs["tool_pk"],
            user=request.user,
            tool_type=Tool.ToolType.API,
        )
        self.operation = APIToolOperation.objects.get(
            pk=kwargs["pk"],
            tool=self.tool,
        )
        return FormView.dispatch(self, request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.operation
        kwargs["tool"] = self.tool
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "API operation updated.")
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tool"] = self.tool
        ctx["operation"] = self.operation
        return ctx


class APIToolOperationDeleteView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
    SuccessMessageMixin,
    DeleteView,
):
    model = APIToolOperation
    template_name = "user_settings/api_tool_operation_confirm_delete.html"
    dashboard_tab = "tools"
    success_message = "API operation deleted."

    def dispatch(self, request, *args, **kwargs):
        self.tool = Tool.objects.get(
            pk=kwargs["tool_pk"],
            user=request.user,
            tool_type=Tool.ToolType.API,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return APIToolOperation.objects.filter(tool=self.tool)

    def get_success_url(self):
        return reverse("user_settings:tool-edit", args=[self.tool.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tool"] = self.tool
        return ctx


# ---------------------------------------------------------------------------#
#  AJAX “Test connection” endpoint                                           #
# ---------------------------------------------------------------------------#
@login_required
@require_POST
async def tool_test_connection(request, pk: int):
    tool = await sync_to_async(Tool.objects.get)(pk=pk, user=request.user)

    try:
        # Extract POST payload
        payload = request.POST
        resolved_connection_mode = ""

        # Get or create credential
        cred, _created = await sync_to_async(
            ToolCredential.objects.get_or_create
        )(
            user=request.user,
            tool=tool,
            defaults={"auth_type": "none"},
        )

        # For built-in tools, extract config from metadata fields
        if tool.tool_type == Tool.ToolType.BUILTIN:
            meta = _get_builtin_metadata_for_tool(tool)
            if meta and meta.get("config_fields"):
                # Start with existing config
                config_data = cred.config.copy()

                # Secret fields (passwords, tokens) are not sent in POST if not modified
                secret_fields = (
                    "password",
                    "app_password",
                    "token",
                    "client_secret",
                    "refresh_token",
                    "access_token",
                )

                # Update with form data if provided
                for field in meta["config_fields"]:
                    field_name = field["name"]
                    if field_name in payload:
                        # For secret fields, preserve existing value if POST value is empty
                        if field_name in secret_fields and payload[field_name] == "" and field_name in cred.config:
                            config_data[field_name] = cred.config[field_name]
                        else:
                            config_data[field_name] = payload[field_name]

                # Update credential config
                cred.config = config_data
                await sync_to_async(cred.save)()
        else:
            form = ToolCredentialForm(
                data=payload,
                instance=cred,
                user=request.user,
                tool=tool,
            )
            if not form.is_valid():
                return JsonResponse(
                    {
                        "status": "error",
                        "message": _first_form_error(form),
                        "errors": form.errors.get_json_data(),
                    }
                )
            resolved_connection_mode = str(
                form.cleaned_data.get("connection_mode") or ""
            ).strip().lower()
            cred = await sync_to_async(form.save, thread_sensitive=True)()

        # Built-in tools with test function
        if tool.tool_type == Tool.ToolType.BUILTIN:
            meta = _get_builtin_metadata_for_tool(tool)
            test_handler = meta.get("test_connection_handler") if meta else None
            if test_handler:
                if tool.tool_subtype in {"email", "caldav"}:
                    result = await test_handler(user=request.user, tool_id=tool.id)
                else:
                    result = await test_handler(tool=tool)
                return JsonResponse(result)

        # MCP
        if tool.tool_type == Tool.ToolType.MCP:
            try:
                connection_action = str(payload.get("connection_action") or "test").strip().lower()

                if resolved_connection_mode == "oauth_managed":
                    if connection_action == "connect_oauth":
                        callback_url = request.build_absolute_uri(
                            reverse("user_settings:mcp-oauth-callback")
                        )
                        flow = await mcp_oauth_service.start_mcp_oauth_flow(
                            tool=tool,
                            credential=cred,
                            user=request.user,
                            redirect_uri=callback_url,
                        )
                        return JsonResponse(
                            {
                                "status": "oauth_redirect",
                                "message": "OAuth authorization required.",
                                "authorization_url": flow.authorization_url,
                            }
                        )
                    if connection_action != "verify":
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": "Managed OAuth uses the dedicated Connect or Verify actions.",
                            }
                        )
                    try:
                        await mcp_oauth_service.get_valid_mcp_access_token(
                            tool=tool,
                            credential=cred,
                            user=request.user,
                        )
                    except mcp_oauth_service.MCPOAuthConnectionRequired:
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": "OAuth connection required. Use Connect with OAuth to authorize this MCP server.",
                            }
                        )
                    except mcp_oauth_service.MCPReconnectRequired:
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": "Reconnect required. Use Connect with OAuth to refresh this MCP server connection.",
                            }
                        )
                elif connection_action not in {"", "test"}:
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": "This action is only available for Managed OAuth.",
                        }
                    )

                client = MCPClient(
                    endpoint=tool.endpoint,
                    credential=cred,
                    transport_type=tool.transport_type,
                    user_id=request.user.id,
                )
                tools = await client.alist_tools(force_refresh=True)
                count = len(tools)
                message = (
                    "Success connecting – no tools found"
                    if count == 0
                    else f"Success connecting – {count} tool{'s' if count > 1 else ''} found"
                )
                return JsonResponse(
                    {"status": "success", "message": message, "tools": tools}
                )
            except Exception as e:  # noqa: BLE001 – broad catch on purpose
                logger.error(e)
                return JsonResponse({"status": "error", "message": str(e)})

        return JsonResponse(
            {
                "status": "not_implemented",
                "message": "No test implemented for this tool type",
            }
        )

    except Exception as e:  # noqa: BLE001 – broad catch on purpose
        logger.error(e)
        return JsonResponse({"status": "error", "message": str(e)})


@login_required
def mcp_oauth_callback(request):
    state = str(request.GET.get("state") or "").strip()
    code = str(request.GET.get("code") or "").strip()
    error = str(request.GET.get("error") or "").strip()
    error_description = str(request.GET.get("error_description") or "").strip()

    if error:
        messages.error(request, f"OAuth authorization failed: {error_description or error}")
        return HttpResponseRedirect(reverse("user_settings:tools"))

    try:
        tool, credential = async_to_sync(mcp_oauth_service.complete_mcp_oauth_flow)(
            user=request.user,
            state=state,
            code=code,
        )
        client = MCPClient(
            endpoint=tool.endpoint,
            credential=credential,
            transport_type=tool.transport_type,
            user_id=request.user.id,
        )
        async_to_sync(client.alist_tools)(force_refresh=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("MCP OAuth callback failed: %s", exc)
        messages.error(request, str(exc))
        return HttpResponseRedirect(reverse("user_settings:tools"))

    messages.success(request, f'MCP OAuth connected successfully for "{tool.name}".')
    return HttpResponseRedirect(reverse("user_settings:tool-edit", args=[tool.pk]))
