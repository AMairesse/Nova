from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field

from nova.forms import (
    LLMProviderForm as _LLMProviderForm,
    AgentForm as _AgentForm,
    ToolForm as _ToolForm,
    ToolCredentialForm as _ToolCredentialForm,
    UserParametersForm as _UserParametersForm,
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
            # Display the field anyway (js will hide it if needed)
            self.fields["api_key"].widget.attrs.setdefault("class", "")

    class Media:
        js = ["user_settings/provider.js"]


# ─── Agent ────────────────────────────────────────────────────────────────
class AgentForm(_AgentForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if user is not None:
            if "tools" in self.fields:
                self.fields["tools"].queryset = Tool.objects.filter(user=user)
            if "sub_agents" in self.fields:
                self.fields["sub_agents"].queryset = Agent.objects.filter(
                    user=user, is_tool=True
                )

        # ---------- Crispy helper + layout ----------
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        self.helper.layout = Layout(
            "name",
            "llm_provider",
            "system_prompt",
            "recursion_limit",
            Field("is_tool", wrapper_class="mb-2"),
            Div(
                "tool_description",
                css_id="tool-description-wrapper",
                css_class="ms-3",
            ),
            "tools",
            "agent_tools",
        )

    class Media:
        js = ["user_settings/agent.js"]


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


# ─── General settings (Langfuse) ───────────────────────────────────────────
class UserParametersForm(_UserParametersForm):
    """Swallow the extra 'user' kwarg injected by OwnerFormKwargsMixin."""
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)