# nova/forms.py
"""
Legacy Nova forms.

Only UserProfileForm remains here; all other canonical forms have been
moved to *user_settings.forms* and are re-exported for backward-compat.

This file can be deleted once all legacy views are removed.
"""
from __future__ import annotations

from typing import Any

from django import forms

from nova.models.models import Agent, UserProfile
# Re-export canonical forms from the new location
from user_settings.forms import (  # noqa: F401
    LLMProviderForm,
    AgentForm,
    ToolForm,
    ToolCredentialForm,
    UserParametersForm,
)


# --------------------------------------------------------------------------- #
#  Still-legacy forms                                                         #
# --------------------------------------------------------------------------- #
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["default_agent"]

    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if user:
            self.fields["default_agent"].queryset = Agent.objects.filter(
                user=user
            )
        else:
            self.fields["default_agent"].queryset = Agent.objects.none()
