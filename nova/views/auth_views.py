from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import urlencode


class NovaLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        if settings.NOVA_AUTH_MODE == "oidc_only":
            target = reverse("social:begin", kwargs={"backend": "oidc"})
            if request.GET.get("next"):
                target += "?" + urlencode({"next": request.GET["next"]})
            return redirect(target)
        return super().dispatch(request, *args, **kwargs)


def block_local_auth_in_oidc_only(view):
    def wrapped(request, *args, **kwargs):
        if settings.NOVA_AUTH_MODE == "oidc_only":
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped
