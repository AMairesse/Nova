# user_settings/forms.py
"""
Canonical forms for both *user_settings* and legacy Nova views.

Every form accepts an optional ``user=…`` kwarg so that any
OwnerFormKwargsMixin (or legacy view) can safely inject it.
All comments are in English.
"""
from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field

from nova.models.models import (
    Agent,
    LLMProvider,
    ProviderType,
    Tool,
    ToolCredential,
    UserParameters,
    UserInfo,
)

from user_settings.mixins import SecretPreserveMixin

# ────────────────────────────────────────────────────────────────────────────
#  Helpers / constants
# ────────────────────────────────────────────────────────────────────────────
PROVIDER_NO_KEY = {"ollama", "lmstudio"}


# ────────────────────────────────────────────────────────────────────────────
#  LLM providers
# ────────────────────────────────────────────────────────────────────────────
class LLMProviderForm(SecretPreserveMixin, forms.ModelForm):
    """Create and edit LLMProvider objects."""
    secret_fields = ["api_key"]

    class Meta:
        model = LLMProvider
        fields = [
            "name",
            "provider_type",
            "model",
            "api_key",
            "base_url",
            "additional_config",
            "max_context_tokens",
        ]
        widgets = {
            "api_key": forms.PasswordInput(render_value=False),
            "additional_config": forms.HiddenInput(),
            "max_context_tokens": forms.NumberInput(attrs={"min": 512}),
        }

    # ------------------------------------------------------------------ #
    #  Constructor                                                        #
    # ------------------------------------------------------------------ #
    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        # Default max-context-tokens for new objects
        if not self.instance.pk:
            ptype = (
                self.data.get("provider_type")
                or self.initial.get("provider_type")
            )
            if ptype in {ProviderType.OPENAI, ProviderType.MISTRAL}:
                self.initial.setdefault("max_context_tokens", 100_000)
            else:
                self.initial.setdefault("max_context_tokens", 4_096)

        # Make api_key optional for local providers
        ptype_current = (
            self.initial.get("provider_type")
            or getattr(self.instance, "provider_type", None)
        )
        if ptype_current in PROVIDER_NO_KEY:
            self.fields["api_key"].required = False

    # ------------------------------------------------------------------ #
    #  Validation helpers                                                 #
    # ------------------------------------------------------------------ #
    def clean_api_key(self) -> str:
        """Preserve encrypted value when the field is left blank."""
        data = self.cleaned_data.get("api_key", "")
        if not data and self.instance.pk:
            return self.instance.api_key
        return data

    def clean_max_context_tokens(self) -> int:
        data = self.cleaned_data.get("max_context_tokens")
        if data is None and self.instance.pk:
            return self.instance.max_context_tokens
        if data is not None and data < 512:
            raise forms.ValidationError(
                _("Max context tokens must be at least 512.")
            )
        return data

    class Media:
        js = ["user_settings/provider.js"]


# ────────────────────────────────────────────────────────────────────────────
#  Agents
# ────────────────────────────────────────────────────────────────────────────
class AgentForm(forms.ModelForm):
    """Full-featured agent form with Crispy layout."""

    # Agents that can be used as tools
    agent_tools = forms.ModelMultipleChoiceField(
        queryset=Agent.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Agents to use as tools"),
    )

    class Meta:
        model = Agent
        fields = [
            "name",
            "llm_provider",
            "system_prompt",
            "recursion_limit",
            "is_tool",
            "tools",
            "agent_tools",
            "tool_description",
        ]

    # ------------------------------------------------------------------ #
    #  Constructor                                                        #
    # ------------------------------------------------------------------ #
    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        # Restrict querysets to the current user (or public objects)
        if user:
            self.fields["llm_provider"].queryset = LLMProvider.objects.filter(
                Q(user=user) | Q(user__isnull=True)
            )
            self.fields["tools"].queryset = Tool.objects.filter(
                Q(user=user) | Q(user__isnull=True)
            )
            self.fields["agent_tools"].queryset = Agent.objects.filter(
                user=user, is_tool=True
            ).exclude(pk=self.instance.pk if self.instance.pk else None)

        # Pre-select sub-agents when editing
        if self.instance.pk:
            self.fields["agent_tools"].initial = self.instance.agent_tools.all()

        # Crispy-forms helper
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

    # ------------------------------------------------------------------ #
    #  Validation                                                         #
    # ------------------------------------------------------------------ #
    def clean_agent_tools(self):
        tools = self.cleaned_data.get("agent_tools")
        # Prevent self-reference
        if self.instance.pk and tools.filter(pk=self.instance.pk).exists():
            tools = tools.exclude(pk=self.instance.pk)
        return tools

    def clean(self):
        data = super().clean()
        if data.get("is_tool") and not (data.get("tool_description") or "").strip():
            self.add_error(
                "tool_description",
                _("Required when using an agent as a tool."),
            )
        return data

    class Media:
        js = ["user_settings/agent.js"]


# ────────────────────────────────────────────────────────────────────────────
#  Tools
# ────────────────────────────────────────────────────────────────────────────
class ToolForm(forms.ModelForm):
    """
    Enhanced tool form — keeps legacy dynamic behaviour and adds
    Crispy helper to avoid nested <form> tags in HTMX fragments.
    """

    tool_subtype = forms.ChoiceField(
        required=False,
        label=_("Builtin tool subtype"),
        help_text=_("Select a builtin tool subtype"),
    )

    input_schema = forms.JSONField(
        widget=forms.Textarea(attrs={"rows": 5, "class": "json-editor"}),
        required=False,
        initial={},
        help_text=_("JSON schema describing the tool inputs"),
    )

    output_schema = forms.JSONField(
        widget=forms.Textarea(attrs={"rows": 5, "class": "json-editor"}),
        required=False,
        initial={},
        help_text=_("JSON schema describing the tool outputs"),
    )

    class Meta:
        model = Tool
        fields = [
            "name",
            "description",
            "tool_type",
            "tool_subtype",
            "endpoint",
            "transport_type",
            "input_schema",
            "output_schema",
            "is_active",
        ]

    # ------------------------------------------------------------------ #
    #  Constructor                                                        #
    # ------------------------------------------------------------------ #
    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user

        # ----------------------------------------------------------------
        # Duplicate POST data for BUILTIN so that the form sees defaults
        # ----------------------------------------------------------------
        if args and hasattr(args[0], "get"):
            data = args[0]
            if (
                data.get("tool_type") == Tool.ToolType.BUILTIN
                and data.get("tool_subtype")
            ):
                data = data.copy()            # QueryDict -> mutable copy
                from nova.tools import get_tool_type

                meta = get_tool_type(data["tool_subtype"])
                if meta:
                    data["name"] = meta["name"]
                    data["description"] = meta["description"]
                    data["python_path"] = meta["python_path"]
                args = (data,) + args[1:]

        super().__init__(*args, **kwargs)

        # Crispy helper
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        # Built-in tools: name / description optional
        self.fields["name"].required = False
        self.fields["description"].required = False

        # Populate subtype choices
        from nova.tools import get_available_tool_types

        self.fields["tool_subtype"].choices = [("", "---------")] + [
            (k, v["name"]) for k, v in get_available_tool_types().items()
        ]

        # Dynamic field requirements
        ttype = (
            self.initial.get("tool_type")
            or getattr(self.instance, "tool_type", None)
            or self.data.get("tool_type")
        )
        if ttype in {"python", "filesystem"} and "auth" in self.fields:
            self.fields["auth"].required = False

        # Pre-fill JSON editors when editing a non-builtin tool
        if self.instance.pk and self.instance.tool_type != Tool.ToolType.BUILTIN:
            if self.instance.input_schema:
                self.fields["input_schema"].initial = self.instance.input_schema
            if self.instance.output_schema:
                self.fields["output_schema"].initial = self.instance.output_schema

    # ------------------------------------------------------------------ #
    #  Validation                                                         #
    # ------------------------------------------------------------------ #
    def clean(self):
        cleaned = super().clean()
        ttype = cleaned.get("tool_type")
        tool_subtype = cleaned.get("tool_subtype")

        if ttype == Tool.ToolType.BUILTIN:
            if not tool_subtype:
                raise forms.ValidationError(
                    _("A BUILTIN tool must have a subtype defined.")
                )
            from nova.tools import get_tool_type

            meta = get_tool_type(tool_subtype)
            if meta:
                cleaned["name"] = meta["name"]
                cleaned["description"] = meta["description"]
                cleaned["python_path"] = meta["python_path"]
                cleaned["input_schema"] = (
                    cleaned.get("input_schema") or meta.get("input_schema", {})
                )
                cleaned["output_schema"] = (
                    cleaned.get("output_schema") or meta.get("output_schema", {})
                )
        else:
            # API / MCP validation
            for f in ["name", "description", "endpoint"]:
                if not cleaned.get(f):
                    self.add_error(f, _("This field is required."))

        return cleaned

    def save(self, commit: bool = True):
        instance: Tool = super().save(commit=False)

        if (
            instance.tool_type == Tool.ToolType.BUILTIN
            and self.cleaned_data.get("python_path")
        ):
            instance.python_path = self.cleaned_data["python_path"]

        if commit:
            instance.save()
        return instance

    class Media:
        js = ["user_settings/tool.js"]


# ────────────────────────────────────────────────────────────────────────────
#  Tool credentials
# ────────────────────────────────────────────────────────────────────────────
class ToolCredentialForm(SecretPreserveMixin, forms.ModelForm):
    """Handle credentials for tools and store tool-specific config."""

    secret_fields = ("password", "token", "client_secret",
                     "refresh_token", "access_token")

    # Example of a tool-specific config field
    caldav_url = forms.URLField(
        required=False,
        help_text=_("CalDav server URL"),
        empty_value="https://",
    )

    class Meta:
        model = ToolCredential
        fields = [
            "auth_type",
            "username",
            "password",
            "token",
            "token_type",
            "client_id",
            "client_secret",
        ]
        widgets = {
            "password": forms.PasswordInput(render_value=True),
            "client_secret": forms.PasswordInput(render_value=True),
        }

    # ------------------------------------------------------------------ #
    #  Constructor                                                        #
    # ------------------------------------------------------------------ #
    def __init__(self, *args: Any, user=None, tool: Tool | None = None, **kw):
        self.user = user
        self.tool = kw.pop("tool", tool)
        super().__init__(*args, **kw)

        # Crispy helper
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        # Pre-fill config
        if self.instance.pk and self.instance.config:
            self.fields["caldav_url"].initial = self.instance.config.get(
                "caldav_url"
            )

        # Hide irrelevant fields based on auth_type
        auth_type = (
            self.instance.auth_type
            if self.instance.pk
            else self.initial.get("auth_type", "basic")
        )

        def hide(fields: list[str]):
            for fld in fields:
                self.fields[fld].widget = forms.HiddenInput()

        if auth_type == "none":
            hide(
                [
                    "username",
                    "password",
                    "token",
                    "token_type",
                    "client_id",
                    "client_secret",
                ]
            )
        elif auth_type == "basic":
            hide(["token", "token_type", "client_id", "client_secret"])
        elif auth_type in {"token", "api_key"}:
            hide(["username", "password", "client_id", "client_secret"])
        elif auth_type == "oauth":
            hide(["username", "password", "token", "token_type"])

        # Tool-specific requirement
        if self.tool and "CalDav" in self.tool.name:
            self.fields["caldav_url"].required = True
        else:
            self.fields["caldav_url"].widget = forms.HiddenInput()

    # ------------------------------------------------------------------ #
    #  Save                                                               #
    # ------------------------------------------------------------------ #
    def save(self, commit: bool = True):
        instance: ToolCredential = super().save(commit=False)

        # Persist tool-specific config
        config = instance.config or {}
        if self.cleaned_data.get("caldav_url"):
            config["caldav_url"] = self.cleaned_data["caldav_url"]
        instance.config = config

        if commit:
            instance.save()
        return instance


# ────────────────────────────────────────────────────────────────────────────
#  User-level parameters
# ────────────────────────────────────────────────────────────────────────────
class UserParametersForm(SecretPreserveMixin, forms.ModelForm):
    """Per-user extra parameters (Langfuse, etc.)."""
    secret_fields = ('langfuse_secret_key',)

    # Read-only field to display API token status
    api_token_status = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control-plaintext'}),
        label=_("API Token Status"),
    )

    class Meta:
        model = UserParameters
        fields = [
            "allow_langfuse",
            "langfuse_public_key",
            "langfuse_secret_key",
            "langfuse_host",
            "api_token_status",
        ]
        widgets = {
            "langfuse_public_key": forms.TextInput(),
            "langfuse_secret_key": forms.PasswordInput(render_value=False),
        }

    # Swallow the extra ``user`` kwarg injected by OwnerFormKwargsMixin
    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        # Set API token status
        if self.instance and self.instance.pk:
            if self.instance.has_api_token:
                self.fields['api_token_status'].initial = _("Active - Token exists")
            else:
                self.fields['api_token_status'].initial = _("No token generated")
        else:
            self.fields['api_token_status'].initial = _("No token generated")

        # Crispy forms helper for better layout
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Div(
                Field("allow_langfuse"),
                Field("langfuse_public_key"),
                Field("langfuse_secret_key"),
                Field("langfuse_host"),
                css_class="mb-4"
            ),
            Div(
                Field("api_token_status"),
                css_class="mb-3"
            )
        )


# ────────────────────────────────────────────────────────────────────────────
#  User Information (Memory)
# ────────────────────────────────────────────────────────────────────────────
class UserInfoForm(forms.ModelForm):
    """Form for editing user memory information in Markdown format."""

    class Meta:
        model = UserInfo
        fields = ["markdown_content"]
        widgets = {
            "markdown_content": forms.Textarea(attrs={
                "rows": 20,
                "placeholder": ("# Personal Info\n\n## Preferences\n"
                                "- Favorite color: Blue\n- Preferred language: English\n\n"
                                "## Work\n- Current project: Nova\n- Role: Developer\n\n"
                                "## Other\n- Hobbies: Reading, coding")
            }),
        }

    # ------------------------------------------------------------------ #
    #  Constructor                                                        #
    # ------------------------------------------------------------------ #
    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        # Crispy forms helper
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
