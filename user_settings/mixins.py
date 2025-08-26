from django.contrib import messages
from django.http import Http404
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, UpdateView, DeleteView


class UserOwnedQuerySetMixin:
    """Ne renvoie que les objets de l’utilisateur (ou publics)."""
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        return qs.filter(user__in=[user, None])


class OwnerFormKwargsMixin:
    """Injecte request.user dans les kwargs du formulaire."""
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class OwnerAccessMixin:
    """Empêche d’accéder à un objet d’un autre utilisateur."""
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.user and obj.user != self.request.user:
            raise Http404("Not allowed")
        return obj


class SuccessMessageMixin:
    success_message = _("Saved successfully")

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


class OwnerCreateView(OwnerFormKwargsMixin, SuccessMessageMixin, CreateView):
    success_message = _("Created successfully")

    def form_valid(self, form):
        if hasattr(form.instance, "user") and not form.instance.user_id:
            form.instance.user = self.request.user
        return super().form_valid(form)


class OwnerUpdateView(
    OwnerAccessMixin, OwnerFormKwargsMixin, SuccessMessageMixin, UpdateView
):
    success_message = _("Updated successfully")


class OwnerDeleteView(OwnerAccessMixin, SuccessMessageMixin, DeleteView):
    success_message = _("Deleted successfully")
    success_url = reverse_lazy("user_settings:providers")   # valeur par défaut
