# user_settings/views/tool.py
from __future__ import annotations
import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DeleteView, FormView
from django import forms
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required

from asgiref.sync import sync_to_async

from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerCreateView,
    OwnerUpdateView,
    OwnerAccessMixin,
    SuccessMessageMixin,
    DashboardRedirectMixin,
)
from user_settings.forms import ToolForm, ToolCredentialForm
from nova.models.models import Tool, ToolCredential
from nova.tools import get_metadata
from nova.mcp.client import MCPClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Helpers                                                           #
# ------------------------------------------------------------------ #
ToolCredentialFormSet = inlineformset_factory(
    Tool,
    ToolCredential,
    form=ToolCredentialForm,
    fields="__all__",
    extra=1,
    can_delete=True,
)


class ToolListView(LoginRequiredMixin,
                   UserOwnedQuerySetMixin,
                   ListView):
    model = Tool
    template_name = "user_settings/tool_list.html"
    context_object_name = "tools"
    paginate_by = 20
    ordering = ["name"]

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/tool_table.html"]
        return super().get_template_names()


# ------------------------------------------------------------------ #
#  CREATE / UPDATE mixin                                             #
# ------------------------------------------------------------------ #
class _ToolBaseMixin(LoginRequiredMixin, SuccessMessageMixin):
    model = Tool
    form_class = ToolForm
    template_name = "user_settings/tool_form.html"
    dashboard_tab = "tools"

    # -------- redirection vers le bon onglet ------------------------
    def get_success_url(self):
        base = reverse_lazy("user_settings:dashboard")
        return f"{base}?from={self.dashboard_tab}"

    # -------- kwargs supplémentaires pour le form ------------------
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    # -------- inline formset (credentials) -------------------------
    def get_formset(self, form, **kwargs):
        return ToolCredentialFormSet(
            instance=form.instance,
            data=self.request.POST if self.request.method == "POST" else None,
            files=self.request.FILES if self.request.method == "POST" else None,
            prefix="cred",
            form_kwargs={"user": self.request.user},
            **kwargs,
        )

    # -------- logique de sauvegarde complète -----------------------
    def form_valid(self, form):
        formset = self.get_formset(form)
        if not formset.is_valid():
            return self.form_invalid(form, formset=formset)

        # 1) Sauvegarde principale
        is_new = form.instance.pk is None
        obj = form.save(commit=False)
        if is_new:
            obj.user = self.request.user
        obj.save()
        if hasattr(form, "save_m2m"):
            form.save_m2m()

        # 2) Credentials
        formset.instance = obj
        formset.save()

        self.object = obj
        return HttpResponseRedirect(
            reverse("user_settings:tool-configure", args=[obj.pk])
            if is_new else self.get_success_url()
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "formset" not in ctx:
            ctx["formset"] = self.get_formset(ctx["form"])
        return ctx


class ToolCreateView(DashboardRedirectMixin,
                     _ToolBaseMixin,
                     OwnerCreateView):
    success_message = "Tool created successfully"


class ToolUpdateView(DashboardRedirectMixin,
                     _ToolBaseMixin,
                     OwnerUpdateView):
    success_message = "Tool updated successfully"


class ToolDeleteView(DashboardRedirectMixin,
                     LoginRequiredMixin,
                     OwnerAccessMixin,
                     SuccessMessageMixin,
                     DeleteView):
    model = Tool
    template_name = "user_settings/tool_confirm_delete.html"
    success_message = "Tool deleted successfully"

    def get_success_url(self):
        base = reverse_lazy("user_settings:dashboard")
        return f"{base}?from=tools"


# ------------------------------------------------------------------ #
#  Configure view                                                    #
# ------------------------------------------------------------------ #
class _BuiltInConfigForm(forms.Form):
    """
    Dynamic form for built-in tools that declare `config_fields`
    in their metadata (e.g. CalDav).
    """

    def __init__(self, *args, meta: dict, initial=None, **kwargs):
        # Remove keys that BaseForm does not understand (e.g. "user")
        kwargs.pop("user", None)

        super().__init__(*args, initial=initial or {}, **kwargs)

        for field in meta.get("config_fields", []):
            ftype = field["type"]
            required = field.get("required", False)
            name = field["name"]
            label = field["label"]

            if ftype == "password":
                self.fields[name] = forms.CharField(
                    label=label,
                    required=required,
                    widget=forms.PasswordInput,
                )
            elif ftype == "url":
                self.fields[name] = forms.URLField(
                    label=label,
                    required=required,
                )
            else:  # default to plain text
                self.fields[name] = forms.CharField(
                    label=label,
                    required=required,
                )


class ToolConfigureView(LoginRequiredMixin, FormView):
    template_name = "user_settings/tool_configure.html"

    def dispatch(self, request, *args, **kwargs):
        self.tool: Tool = Tool.objects.get(
            pk=kwargs["pk"], user=self.request.user
        )
        return super().dispatch(request, *args, **kwargs)

    # ------- choose the proper form class ---------------------------
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

        # Stay on configure page
        return HttpResponseRedirect(self.request.path)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tool"] = self.tool
        return ctx


# ------------------------------------------------------------------ #
#  AJAX “Test connection” endpoint                                   #
# ------------------------------------------------------------------ #
@method_decorator(login_required, name="dispatch")
@require_POST
async def tool_test_connection(request, pk: int):
    tool = await sync_to_async(Tool.objects.get)(pk=pk, user=request.user)
    # Re-use almost all of the legacy logic -------------------------
    try:
        # Extract POST params
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
            if username: cred.username = username
            if password: cred.password = password
            if token:    cred.token = token
            cred.config.update(
                {"caldav_url": caldav_url, "username": username, "password": password or cred.config.get("password", "")}
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
                    "Success connecting - no tools found"
                    if count == 0
                    else f"Success connecting - {count} tool{'s' if count > 1 else ''} found"
                )
                return JsonResponse({"status": "success", "message": message, "tools": tools})
            except Exception as e:
                logger.error(e)
                return JsonResponse({"status": "error", "message": str(e)})

        return JsonResponse({"status": "not_implemented", "message": "No test implemented for this tool type"})

    except Exception as e:
        logger.error(e)
        return JsonResponse({"status": "error", "message": str(e)})