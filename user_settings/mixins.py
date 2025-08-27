# user_settings/mixins.py
"""
Reusable mixins for the *user_settings* application.

• All comments are in English (see contribution guidelines).
"""
from django import forms
from django.contrib import messages
from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, UpdateView, DeleteView


# ---------------------------------------------------------------------------#
#  Query / form / ownership helpers                                          #
# ---------------------------------------------------------------------------#
class UserOwnedQuerySetMixin:
    """Return only objects belonging to the current user (or public ones)."""
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        return qs.filter(user__in=[user, None])


class OwnerFormKwargsMixin:
    """Inject `request.user` in the form's kwargs."""
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class OwnerAccessMixin:
    """Deny access to objects owned by another user."""
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.user and obj.user != self.request.user:
            raise Http404("Not allowed")
        return obj


class SuccessMessageMixin:
    """Display a translatable *success* banner after `form_valid()`."""
    success_message = _("Saved successfully")

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


class OwnerCreateView(OwnerFormKwargsMixin, SuccessMessageMixin, CreateView):
    """Create-view that automatically links the new object to the user."""
    success_message = _("Created successfully")

    def form_valid(self, form):
        if hasattr(form.instance, "user") and not form.instance.user_id:
            form.instance.user = self.request.user
        return super().form_valid(form)


class OwnerUpdateView(
    OwnerAccessMixin, OwnerFormKwargsMixin, SuccessMessageMixin, UpdateView
):
    """Update-view restricted to the owner only."""
    success_message = _("Updated successfully")


class OwnerDeleteView(OwnerAccessMixin, SuccessMessageMixin, DeleteView):
    """Delete-view restricted to the owner only."""
    success_message = _("Deleted successfully")


# ---------------------------------------------------------------------------#
#  Keep old secret if blank                                                  #
# ---------------------------------------------------------------------------#
class SecretPreserveMixin(forms.ModelForm):
    """
    Keeps the existing secret when the user submits an empty value.
    Also shows a placeholder so the UI hints that a secret is already stored.

    Usage:
        class ToolCredentialForm(SecretPreserveMixin, forms.ModelForm):
            secret_fields = ("password",)           # REQUIRED
            class Meta:
                model = ToolCredential
                fields = ("caldav_url", "username", "password")
    """

    #: tuple[str, ...] – names of ModelForm fields considered “secret”
    secret_fields: tuple[str, ...] = ()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    _KEEP_MSG = _("A value exists, leave blank to keep")

    def _decorate_secret_widget(self, field_name: str) -> None:
        """
        Adds the placeholder and makes sure real value is never rendered.
        """
        field = self.fields[field_name]
        # Do not leak the real value in the HTML.
        if hasattr(field.widget, "render_value"):
            field.widget.render_value = False
        field.widget.attrs.setdefault("placeholder", self._KEEP_MSG)
        # Mark optional so an empty POST is accepted.
        field.required = False

    # ------------------------------------------------------------------ #
    # Django hooks
    # ------------------------------------------------------------------ #
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:  # editing an existing row
            for name in self.secret_fields:
                # Field may be absent in a given subclass -> ignore silently.
                if name in self.fields and getattr(self.instance, name):
                    self._decorate_secret_widget(name)

    def clean(self):
        """
        Centralised preservation logic.
        """
        cleaned = super().clean()

        if self.instance.pk:  # only relevant in edit mode
            for name in self.secret_fields:
                if name not in cleaned:
                    continue
                # If user left the field blank, keep existing secret.
                if cleaned[name] in ("", None, b""):
                    cleaned[name] = getattr(self.instance, name)

        return cleaned


# ---------------------------------------------------------------------------#
#  Dashboard redirection helper                                              #
# ---------------------------------------------------------------------------#
class DashboardRedirectMixin:
    """
    Uniform redirection helper.

    1. Every *form* template must embed:
         <input type="hidden" name="from" value="{{ view.dashboard_tab }}">
    2. If the request carries `?from=<tab>` (GET or POST), the mixin sends the
       user back to the dashboard with the correct anchor: `/#pane-<tab>`.
    3. If the parameter is missing but the view declares `dashboard_tab`,
       the mixin falls back to that tab.
    """

    dashboard_tab: str | None = None   # must be set in subclasses

    # ------------------------------------------------------------------ #
    #  Helpers                                                           #
    # ------------------------------------------------------------------ #
    def _dashboard_url(self) -> str:
        return reverse("user_settings:dashboard")

    def _anchor(self, tab: str | None) -> str:
        return "" if not tab else f"#pane-{tab}"

    # ------------------------------------------------------------------ #
    #  Main entry point                                                  #
    # ------------------------------------------------------------------ #
    def get_success_url(self) -> str:
        origin = self.request.POST.get("from") or self.request.GET.get("from")
        target_tab = origin or self.dashboard_tab
        return f"{self._dashboard_url()}{self._anchor(target_tab)}"
