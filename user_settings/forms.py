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

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import ProviderType, LLMProvider
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserObjects import UserParameters
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
        js = ["user_settings/js/provider.js"]


# ────────────────────────────────────────────────────────────────────────────
#  Agents
# ────────────────────────────────────────────────────────────────────────────
class AgentForm(forms.ModelForm):
    """Full-featured agent form with Crispy layout."""

    # Agents that can be used as tools
    agent_tools = forms.ModelMultipleChoiceField(
        queryset=AgentConfig.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Agents to use as tools"),
    )

    class Meta:
        model = AgentConfig
        fields = [
            "name",
            "llm_provider",
            "system_prompt",
            "recursion_limit",
            "is_tool",
            "tools",
            "agent_tools",
            "tool_description",
            "auto_summarize",
            "token_threshold",
            "preserve_recent",
            "strategy",
            "max_summary_length",
            "summary_model",
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
            self.fields["tools"].label_from_instance = (
                lambda tool: f"{tool.name} (#{tool.id}, {tool.get_tool_type_display()})"
            )
            self.fields["agent_tools"].queryset = AgentConfig.objects.filter(
                user=user, is_tool=True
            ).exclude(pk=self.instance.pk if self.instance.pk else None)

        # Pre-select sub-agents when editing
        if self.instance.pk:
            self.fields["agent_tools"].initial = self.instance.agent_tools.all()

        # Make summarization fields not required (they have model defaults)
        self.fields["auto_summarize"].required = False
        self.fields["token_threshold"].required = False
        self.fields["preserve_recent"].required = False
        self.fields["strategy"].required = False
        self.fields["max_summary_length"].required = False
        self.fields["summary_model"].required = False

        # Crispy-forms helper
        #
        # Layout note:
        # - "tools" uses a custom dual-list widget implemented in JS:
        #   - Available tools vs Selected tools
        #   - The original multi-select is rendered but visually replaced.
        # - "agent_tools" keeps checkbox list for clarity (usually fewer items).
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
            Field(
                "tools",
                css_class="dual-list-tools-source",
            ),
            "agent_tools",
            Div(
                Field("auto_summarize"),
                Field("token_threshold"),
                Field("preserve_recent"),
                Field("strategy"),
                Field("max_summary_length"),
                Field("summary_model"),
                css_class="mt-4 p-3 border rounded",
                css_id="summarization-settings",
            ),
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
        js = ["user_settings/js/agent.js"]


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
                # Keep builtin names editable by users. Use metadata as defaults only.
                if not (cleaned.get("name") or "").strip():
                    cleaned["name"] = meta["name"]
                if not (cleaned.get("description") or "").strip():
                    cleaned["description"] = meta["description"]
                cleaned["python_path"] = meta["python_path"]
                cleaned["input_schema"] = (
                    cleaned.get("input_schema") or meta.get("input_schema", {})
                )
                cleaned["output_schema"] = (
                    cleaned.get("output_schema") or meta.get("output_schema", {})
                )

            if (
                self.user
                and tool_subtype == "email"
                and (cleaned.get("name") or "").strip()
            ):
                duplicate_qs = Tool.objects.filter(
                    user=self.user,
                    tool_type=Tool.ToolType.BUILTIN,
                    tool_subtype="email",
                    name__iexact=cleaned["name"].strip(),
                )
                if self.instance.pk:
                    duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
                if duplicate_qs.exists():
                    self.add_error(
                        "name",
                        _("An email tool with this name already exists. Choose a distinct mailbox alias."),
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
        js = ["user_settings/js/tool.js"]


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
        empty_value=None,
        assume_scheme='https',
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

        # Add data-auth-field attributes for JS visibility control
        auth_fields = ["username", "password", "token", "token_type", "client_id", "client_secret"]
        for field_name in auth_fields:
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.setdefault('data-auth-field', field_name)

        # Note: JS will handle initial visibility based on auth_type

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
            "continuous_default_messages_limit",
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

        limit_field = self.fields["continuous_default_messages_limit"]
        limit_field.label = _("Latest messages")
        limit_field.help_text = _(
            "Shown by default in Continuous when no day is selected."
        )
        existing_classes = limit_field.widget.attrs.get("class", "")
        limit_field.widget.attrs.update(
            {
                "min": UserParameters.CONTINUOUS_DEFAULT_MESSAGES_LIMIT_MIN,
                "max": UserParameters.CONTINUOUS_DEFAULT_MESSAGES_LIMIT_MAX,
                "step": 1,
                "inputmode": "numeric",
                "class": " ".join(filter(None, [existing_classes, "form-control"])),
            }
        )
        limit_field.error_messages.update(
            {
                "min_value": _(
                    "Choose at least %(limit_value)s messages."
                ),
                "max_value": _(
                    "Choose at most %(limit_value)s messages."
                ),
            }
        )

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
                Field("continuous_default_messages_limit"),
                css_class="mb-4",
            ),
            Div(
                Field("api_token_status"),
                css_class="mb-3"
            )
        )


# ────────────────────────────────────────────────────────────────────────────
#  Memory embeddings settings (user-level)
# ────────────────────────────────────────────────────────────────────────────
class UserMemoryEmbeddingsForm(SecretPreserveMixin, forms.ModelForm):
    """Configure embeddings provider for long-term memory.

    Note: llama.cpp system provider (if present) is enforced at runtime.
    The UI can still show these fields, but the backend will prefer llama.cpp.
    """

    secret_fields = ("memory_embeddings_api_key",)

    class Meta:
        model = UserParameters
        fields = [
            "memory_embeddings_enabled",
            "memory_embeddings_url",
            "memory_embeddings_model",
            "memory_embeddings_api_key",
        ]
        widgets = {
            "memory_embeddings_api_key": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Field("memory_embeddings_enabled"),
            Field("memory_embeddings_url"),
            Field("memory_embeddings_model"),
            Field("memory_embeddings_api_key"),
        )

    def clean_memory_embeddings_api_key(self) -> str:
        """Preserve encrypted value when left blank."""
        data = self.cleaned_data.get("memory_embeddings_api_key", "")
        if not data and self.instance.pk:
            return self.instance.memory_embeddings_api_key
        return data
