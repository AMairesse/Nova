# user_settings/mixins.py
"""
Reusable mixins for the *user_settings* application.

• All comments are in English (see contribution guidelines).
"""
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
class SecretPreserveMixin:
    """This mixin keeps old secrets in case the user leaves them blank.
       It's usable with ModelForm or Form."""
    secret_fields: tuple[str, ...] = ()
    _KEEP_MSG = _("Secret exists, leave blank to keep")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Instance/initial dict contenant les secrets déjà présents
        self._existing_secrets = {}
        if hasattr(self, "instance") and getattr(self.instance, "pk", None):
            for f in self.secret_fields:
                self._existing_secrets[f] = getattr(self.instance, f, "")
        else:
            initial = kwargs.get("initial", {})
            for f in self.secret_fields:
                if f in initial:
                    self._existing_secrets[f] = initial[f]

        # Adapter les champs
        for f in self.secret_fields:
            if f in self.fields and self._existing_secrets.get(f):
                fld = self.fields[f]
                fld.required = False
                fld.widget.attrs.setdefault("placeholder", self._KEEP_MSG)
                if hasattr(fld.widget, "render_value"):
                    fld.widget.render_value = False

    def clean(self):
        data = super().clean()
        for f in self.secret_fields:
            if data.get(f) in ("", None) and f in self._existing_secrets:
                data[f] = self._existing_secrets[f]
        return data


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
