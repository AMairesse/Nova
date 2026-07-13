from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import urlencode
from django.views.generic import TemplateView


class NovaLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        if settings.NOVA_AUTH_MODE == "oidc_only":
            target = reverse("nova_oidc_start")
            if request.GET.get("next"):
                target += "?" + urlencode({"next": request.GET["next"]})
            return redirect(target)
        return super().dispatch(request, *args, **kwargs)


class NovaOIDCStartView(TemplateView):
    """Bridge an OIDC-only GET login redirect to social-auth's POST begin view."""

    template_name = "registration/oidc_start.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next"] = self.request.GET.get("next", "")
        return context


def block_local_auth_in_oidc_only(view):
    def wrapped(request, *args, **kwargs):
        if settings.NOVA_AUTH_MODE == "oidc_only":
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped
