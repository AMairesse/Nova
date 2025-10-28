# user_settings/views/api_token.py
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy
from django.views.generic import View
from rest_framework.authtoken.models import Token

from nova.models.UserObjects import UserParameters
from user_settings.mixins import DashboardRedirectMixin


class GenerateAPITokenView(DashboardRedirectMixin, LoginRequiredMixin, View):
    """Generate a new API token for the user."""

    def post(self, request, *args, **kwargs):
        # Delete existing token if any
        Token.objects.filter(user=request.user).delete()

        # Create new token
        token = Token.objects.create(user=request.user)

        # Update user parameters
        user_params, _created = UserParameters.objects.get_or_create(user=request.user)
        user_params.has_api_token = True
        user_params.save()

        # Add token to messages (displayed only once)
        messages.success(
            request,
            gettext_lazy(
                """Your new API token: <strong>{}</strong><br>
                   Please copy this token immediately as it will not be shown again."""
            ).format(token.key),
            extra_tags='safe'
        )

        return HttpResponseRedirect(self.get_success_url())


class DeleteAPITokenView(DashboardRedirectMixin, LoginRequiredMixin, View):
    """Delete the user's API token."""

    def post(self, request, *args, **kwargs):
        # Delete token
        deleted_count, _dict = Token.objects.filter(user=request.user).delete()

        # Update user parameters
        user_params, _created = UserParameters.objects.get_or_create(user=request.user)
        user_params.has_api_token = False
        user_params.save()

        if deleted_count > 0:
            messages.success(request, gettext_lazy("API token has been deleted successfully."))
        else:
            messages.info(request, gettext_lazy("No API token was found to delete."))

        return HttpResponseRedirect(self.get_success_url())
