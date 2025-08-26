from django import forms
from django.utils.translation import gettext_lazy as _

from nova.forms import (
    LLMProviderForm as _LLMProviderForm,
    AgentForm as _AgentForm,
    ToolForm as _ToolForm,
    ToolCredentialForm as _ToolCredentialForm,
)
from nova.models.models import Tool, Agent

# ─── Provider ──────────────────────────────────────────────────────────────
PROVIDER_NO_KEY = {"ollama", "lmstudio"}


class LLMProviderForm(_LLMProviderForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        ptype = (
            self.initial.get("provider_type")
            or getattr(self.instance, "provider_type", None)
        )
        # mark api_key non-required if not needed
        if ptype in PROVIDER_NO_KEY:
            self.fields["api_key"].required = False
            # add bootstrap util to wrapper via crispy’s CSS class helper
            self.fields["api_key"].widget.attrs.setdefault("class", "")
            #self.fields["api_key"].widget.attrs["class"] += " d-none"

    class Media:
        js = ["user_settings/provider.js"]


# ─── Agent ─────────────────────────────────────────────────────────────────
# ─── Agent ────────────────────────────────────────────────────────────────
class AgentForm(_AgentForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        # rename labels only when the field is present
        if "provider" in self.fields:
            self.fields["provider"].label = _("Provider")
        if "tools" in self.fields:
            self.fields["tools"].label = _("Tools")
        if "sub_agents" in self.fields:
            self.fields["sub_agents"].label = _("Agents to use as tools")
        if "is_tool" in self.fields:
            self.fields["is_tool"].label = _("Is tool")
        if "tool_description" in self.fields:
            self.fields["tool_description"].label = _("Tool description")

        # limit querysets to current user
        if user is not None:
            if "tools" in self.fields:
                self.fields["tools"].queryset = Tool.objects.filter(user=user)
            if "sub_agents" in self.fields:
                self.fields["sub_agents"].queryset = Agent.objects.filter(
                    user=user, is_tool=True
                )

    class Media:
        js = ["user_settings/agent.js"]  # toggles description field


# ─── Tool (unchanged wrappers) ─────────────────────────────────────────────
class ToolForm(_ToolForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class ToolCredentialForm(_ToolCredentialForm):
    def __init__(self, *args, user=None, tool=None, **kwargs):
        self.user = user
        self.tool = tool
        super().__init__(*args, **kwargs)
