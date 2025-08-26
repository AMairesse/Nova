from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView

from user_settings.mixins import (
    UserOwnedQuerySetMixin,
    OwnerAccessMixin,
    SuccessMessageMixin,
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
    fields="__all__",          # ToolCredentialForm contrôle les widgets
    extra=1,
    can_delete=True,
)


class ToolListView(LoginRequiredMixin,
                   UserOwnedQuerySetMixin,
                   ListView):
    model = Tool
    template_name = "user_settings/tool_list.html"  # à fournir étape 2
    context_object_name = "tools"
    paginate_by = 20
    ordering = ["name"]


class _ToolBaseMixin(LoginRequiredMixin, SuccessMessageMixin):
    model = Tool
    form_class = ToolForm
    template_name = "user_settings/tool_form.html"
    success_url = reverse_lazy("user_settings:tools")

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
            user=self.request.user,
            prefix="cred",
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


class ToolCreateView(_ToolBaseMixin, CreateView):
    success_message = "Tool created successfully"


class ToolUpdateView(_ToolBaseMixin, OwnerAccessMixin, UpdateView):
    success_message = "Tool updated successfully"


class ToolDeleteView(LoginRequiredMixin,
                     OwnerAccessMixin,
                     SuccessMessageMixin,
                     DeleteView):
    model = Tool
    template_name = "user_settings/tool_confirm_delete.html"
    success_url = reverse_lazy("user_settings:tools")
    success_message = "Tool deleted successfully"
