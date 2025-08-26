from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.urls import reverse_lazy
from django.views.generic import ListView, DeleteView

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


class _ToolBaseMixin(LoginRequiredMixin, SuccessMessageMixin):
    model = Tool
    form_class = ToolForm
    template_name = "user_settings/tool_form.html"
    dashboard_tab = "tools"
    success_url = reverse_lazy("user_settings:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    # Inline Formset --------------------------------------------------
    def get_formset(self, form, **kwargs):
        return ToolCredentialFormSet(
            instance=form.instance,
            data=self.request.POST if self.request.method == "POST" else None,
            files=self.request.FILES if self.request.method == "POST" else None,
            prefix="cred",
            form_kwargs={"user": self.request.user},
            **kwargs,
        )

    def form_valid(self, form):
        formset = self.get_formset(form)
        if formset.is_valid():
            response = super().form_valid(form)
            formset.save()
            return response
        else:
            return self.form_invalid(form, formset=formset)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "formset" not in ctx:
            ctx["formset"] = self.get_formset(ctx["form"])
        return ctx


class ToolCreateView(DashboardRedirectMixin, _ToolBaseMixin, OwnerCreateView):
    success_message = "Tool created successfully"


class ToolUpdateView(DashboardRedirectMixin, _ToolBaseMixin, OwnerUpdateView):
    success_message = "Tool updated successfully"


class ToolDeleteView(DashboardRedirectMixin,
                     LoginRequiredMixin,
                     OwnerAccessMixin,
                     SuccessMessageMixin,
                     DeleteView):
    model = Tool
    template_name = "user_settings/tool_confirm_delete.html"
    success_url = reverse_lazy("user_settings:dashboard")
    success_message = "Tool deleted successfully"
