from django import forms
from django.db.models import Q

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
                self.fields["tools"].queryset = Tool.objects.filter(
                    Q(user=user) | Q(user__isnull=True)
                ).order_by("name")
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

    def clean(self):
        data = super().clean()
        is_tool = data.get("is_tool")
        desc = (data.get("tool_description") or "").strip()

        if is_tool and not desc:
            self.add_error(
                "tool_description",
                forms.ValidationError("La description de l’outil est obligatoire."),
            )
        if not is_tool:
            data["tool_description"] = ""
        return data

    class Media:
        js = ["user_settings/agent.js"]


# ─── Tool ─────────────────────────────────────────────────────────────────
class ToolForm(_ToolForm):
    """
    Formulaire enrichi : ajuste certains champs selon le type d’outil et
    reprend les validations de l’ancien écran legacy.
    """

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        ttype = (
            self.initial.get("tool_type")
            or getattr(self.instance, "tool_type", None)
            or self.data.get("tool_type")
        )

        if ttype in {"python", "filesystem"}:
            # Pas d’auth requise
            if "auth" in self.fields:
                self.fields["auth"].required = False
        else:
            if "auth" in self.fields:
                self.fields["auth"].required = True

    def clean(self):
        cleaned = super().clean()
        ttype = cleaned.get("tool_type")

        if ttype == "api" and not cleaned.get("endpoint"):
            self.add_error("endpoint", "L’endpoint est requis pour un outil API.")
        return cleaned


class ToolCredentialForm(_ToolCredentialForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


# ─── General settings (Langfuse) ───────────────────────────────────────────
class UserParametersForm(_UserParametersForm):
    """Swallow the extra 'user' kwarg injected by OwnerFormKwargsMixin."""
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
