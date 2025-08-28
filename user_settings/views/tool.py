# user_settings/views/tool.py
from __future__ import annotations

import logging
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DeleteView, FormView
from crispy_forms.helper import FormHelper
from asgiref.sync import sync_to_async

from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerAccessMixin,
    SecretPreserveMixin,
    SuccessMessageMixin,
    DashboardRedirectMixin,
)
from user_settings.forms import ToolForm, ToolCredentialForm
from nova.models.models import Tool, ToolCredential
from nova.tools import get_metadata
from nova.mcp.client import MCPClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------#
#  Helpers                                                                   #
# ---------------------------------------------------------------------------#
ToolCredentialFormSet = inlineformset_factory(
    Tool,
    ToolCredential,
    form=ToolCredentialForm,
    fields="__all__",
    extra=1,
    can_delete=True,
)


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


# ---------------------------------------------------------------------------#
#  CREATE / UPDATE (without credential formset)                              #
# ---------------------------------------------------------------------------#
class _ToolBaseMixin(DashboardRedirectMixin,
                     LoginRequiredMixin,
                     SuccessMessageMixin):
    """
    Common logic for Tool create / update views.
    """
    model = Tool
    form_class = ToolForm
    template_name = "user_settings/tool_form.html"
    dashboard_tab = "tools"

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["user"] = self.request.user
        return kw

    def form_valid(self, form):
        """
        Custom save to inject the user and redirect to the *configure* view
        right after creation.
        """
        is_new = form.instance.pk is None

        obj = form.save(commit=False)
        if is_new:
            obj.user = self.request.user
        obj.save()
        if hasattr(form, "save_m2m"):
            form.save_m2m()

        self.object = obj
        messages.success(self.request, "Tool saved successfully.")

        # New tool → go to configuration screen
        if is_new:
            return HttpResponseRedirect(
                reverse("user_settings:tool-configure", args=[obj.pk])
            )

        # Updated tool → back to dashboard tab
        return HttpResponseRedirect(self.get_success_url())

    def form_invalid(self, form):
        """Return HTTP 400 so dev-tools clearly show the validation error."""
        return self.render_to_response(
            self.get_context_data(form=form), status=400
        )


class ToolCreateView(_ToolBaseMixin, OwnerCreateView):
    success_message = "Tool created successfully"


class ToolUpdateView(_ToolBaseMixin, OwnerUpdateView):
    success_message = "Tool updated successfully"


class ToolDeleteView(
    DashboardRedirectMixin,
    LoginRequiredMixin,
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
    secret_fields = ("password", "token", "client_secret", "refresh_token", "access_token")

    def __init__(self, *args, meta: dict, initial=None, **kw):
        # Store existing secrets
        kw.pop("user", None)
        self._existing_secrets = {k: v for k, v in (initial or {}).items()
                                  if k in self.secret_fields}

        super().__init__(*args, initial=initial or {}, **kw)

        # Crispy: no nested <form>
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        # Build dynamic fields
        for cfg in meta.get("config_fields", []):
            ftype = cfg["type"]
            required = cfg.get("required", False)
            name = cfg["name"]
            label = cfg["label"]

            if ftype == "password":
                widget = forms.PasswordInput(render_value=False)
                self.fields[name] = forms.CharField(
                    label=label, required=required, widget=widget
                )
            elif ftype == "url":
                self.fields[name] = forms.URLField(label=label, required=required)
            else:
                self.fields[name] = forms.CharField(label=label, required=required)

        # Preserve existing secrets
        keep_msg = _("Secret exists, leave blank to keep")
        for f in self.secret_fields:
            if f in self.fields and f in self._existing_secrets:
                fld = self.fields[f]
                fld.required = False
                fld.widget.attrs.setdefault("placeholder", keep_msg)

    def clean(self):
        data = super().clean()
        # Preserve existing secrets if the field is left blank
        for f in self.secret_fields:
            if data.get(f) in ("", None) and f in self._existing_secrets:
                data[f] = self._existing_secrets[f]
        return data


class ToolConfigureView(DashboardRedirectMixin, LoginRequiredMixin, FormView):
    template_name = "user_settings/tool_configure.html"

    # ------------------------------------------------------------------ #
    #  Dispatch                                                          #
    # ------------------------------------------------------------------ #
    def dispatch(self, request, *args, **kwargs):
        self.tool: Tool = Tool.objects.get(
            pk=kwargs["pk"], user=self.request.user
        )
        return super().dispatch(request, *args, **kwargs)

    # ------------------------------------------------------------------ #
    #  Build the proper form                                             #
    # ------------------------------------------------------------------ #
    def get_form_class(self):
        if self.tool.tool_type == Tool.ToolType.BUILTIN:
            meta = get_metadata(self.tool.python_path)
            return lambda *a, **kw: _BuiltInConfigForm(*a, meta=meta, **kw)
        return ToolCredentialForm

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        if self.tool.tool_type != Tool.ToolType.BUILTIN:
            # For ToolCredentialForm
            credential, _ = ToolCredential.objects.get_or_create(
                user=self.request.user,
                tool=self.tool,
                defaults={"auth_type": "basic"},
            )
            kw["instance"] = credential
            kw["tool"] = self.tool
        else:
            # For built-in form, preload current config
            cred = self.tool.credentials.first()
            kw["initial"] = cred.config if cred else {}
        kw["user"] = self.request.user
        return kw

    # ------------------------------------------------------------------ #
    #  Save                                                              #
    # ------------------------------------------------------------------ #
    def form_valid(self, form):
        if self.tool.tool_type == Tool.ToolType.BUILTIN:
            cred, _ = ToolCredential.objects.get_or_create(
                user=self.request.user,
                tool=self.tool,
                defaults={"auth_type": "basic"},
            )
            cred.config.update(form.cleaned_data)
            cred.save()
        else:
            form.save()

        messages.success(self.request, "Configuration saved.")
        return HttpResponseRedirect(self.get_success_url())

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
        auth_type = payload.get("auth_type", "basic")
        username = payload.get("username", "")
        password = payload.get("password", "")
        token = payload.get("token", "")
        caldav_url = payload.get("caldav_url", "")

        # Get or create credential
        cred, created = await sync_to_async(
            ToolCredential.objects.get_or_create
        )(
            user=request.user,
            tool=tool,
            defaults={
                "auth_type": auth_type,
                "username": username,
                "password": password,
                "token": token,
                "config": {
                    "caldav_url": caldav_url,
                    "username": username,
                    "password": password,
                },
            },
        )
        if not created:
            cred.auth_type = auth_type
            if username:
                cred.username = username
            if password:
                cred.password = password
            if token:
                cred.token = token
            cred.config.update(
                {
                    "caldav_url": caldav_url,
                    "username": username,
                    "password": password or cred.config.get("password", ""),
                }
            )
            await sync_to_async(cred.save)()

        # Built-in CalDav
        if tool.tool_subtype == "caldav":
            from nova.tools.builtins.caldav import test_caldav_access
            result = await test_caldav_access(request.user, tool.id)
            return JsonResponse(result)

        # MCP
        if tool.tool_type == Tool.ToolType.MCP:
            try:
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
