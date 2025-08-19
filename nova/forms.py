# nova/forms.py
"""
Forms used across the application.

All user-facing strings are wrapped in gettext_lazy for i18n.
"""
from __future__ import annotations

from typing import Any

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from nova.models.models import (
    Agent,
    LLMProvider,
    ProviderType,
    Tool,
    ToolCredential,
    UserParameters,
    UserProfile,
)


# --------------------------------------------------------------------------- #
#  User-level configuration                                                   #
# --------------------------------------------------------------------------- #
class UserParametersForm(forms.ModelForm):
    """Store user credentials and additional user-defined settings."""

    class Meta:
        model = UserParameters
        fields = [
            "allow_langfuse",
            "langfuse_public_key",
            "langfuse_secret_key",
            "langfuse_host"
        ]
        widgets = {
            "langfuse_public_key": forms.TextInput(),
            "langfuse_secret_key": forms.PasswordInput(render_value=False)
        }


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["default_agent"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user:
            # Restrict choices to objects owned by the current user
            self.fields["default_agent"].queryset = Agent.objects.filter(
                user=user
            )
        else:
            self.fields["default_agent"].queryset = Agent.objects.none()


# --------------------------------------------------------------------------- #
#  LLM providers                                                              #
# --------------------------------------------------------------------------- #
class LLMProviderForm(forms.ModelForm):
    """Create and edit LLMProvider objects."""

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
            "max_context_tokens": forms.NumberInput(attrs={'min': 512}),  # UI hint
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Set initial default based on provider_type (for new instances)
        if not self.instance.pk:
            provider_type = self.data.get('provider_type') or self.initial.get('provider_type')
            if provider_type in [ProviderType.OLLAMA, ProviderType.LLMSTUDIO]:
                self.initial['max_context_tokens'] = 4096
            elif provider_type in [ProviderType.OPENAI, ProviderType.MISTRAL]:
                self.initial['max_context_tokens'] = 100000

    def clean_api_key(self) -> str:
        """Preserve the encrypted value if the field is left blank."""
        data = self.cleaned_data.get("api_key", "")
        if not data and self.instance.pk:
            return self.instance.api_key
        return data

    def clean_max_context_tokens(self) -> int:
        """Preserve existing value if not provided, with min validation."""
        data = self.cleaned_data.get("max_context_tokens")
        if data is None and self.instance.pk:
            return self.instance.max_context_tokens
        if data < 512:
            raise forms.ValidationError(_("Max context tokens must be at least 512."))
        return data


# --------------------------------------------------------------------------- #
#  Agents                                                                     #
# --------------------------------------------------------------------------- #
class AgentForm(forms.ModelForm):
    # Agents that can be exposed as tools for other agents
    agent_tools = forms.ModelMultipleChoiceField(
        queryset=Agent.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Agents to expose as tools"),
    )

    class Meta:
        model = Agent
        fields = [
            "name",
            "llm_provider",
            "system_prompt",
            "is_tool",
            "tools",
            "agent_tools",
            "tool_description",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if user:
            # Restrict choices to objects owned by the current user
            self.fields["llm_provider"].queryset = LLMProvider.objects.filter(user=user)

            # Regular tools: owned by user or public built-ins
            self.fields["tools"].queryset = Tool.objects.filter(
                models.Q(user=user) | models.Q(user__isnull=True),
                is_active=True,
            )

            # Other agents that are flagged as tools
            self.fields["agent_tools"].queryset = Agent.objects.filter(
                user=user, is_tool=True
            ).exclude(pk=self.instance.pk if self.instance.pk else None)

        # Pre-select agent_tools when editing
        if self.instance.pk:
            self.fields["agent_tools"].initial = self.instance.agent_tools.all()

    def clean_agent_tools(self):
        tools = self.cleaned_data.get("agent_tools")
        # Prevent an agent from referencing itself
        if self.instance.pk and tools.filter(pk=self.instance.pk).exists():
            tools = tools.exclude(pk=self.instance.pk)
        return tools

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_tool") and not cleaned_data.get("tool_description"):
            self.add_error("tool_description", _("Required when using as tool."))
        return cleaned_data


# --------------------------------------------------------------------------- #
#  Tools                                                                      #
# --------------------------------------------------------------------------- #
class ToolForm(forms.ModelForm):
    """Simplified form to create and edit Tool objects."""

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

    # --------------------------------------------------------------------- #
    #  Constructor                                                          #
    # --------------------------------------------------------------------- #
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        • For BUILTIN tools we only auto-fill *human* metadata
          (name, description, python_path).  
          We NO LONGER copy input/output schemas into the POST data.

        • For API / MCP tools behaviour is unchanged.

        • The choice list for tool_subtype is still constructed
          at runtime from nova.tools.get_available_tool_types().
        """
        # ---- 1) Inspect and possibly duplicate POST data -----------------
        if args and hasattr(args[0], "get"):
            data = args[0]
            if (
                data.get("tool_type") == Tool.ToolType.BUILTIN
                and data.get("tool_subtype")
            ):
                data = data.copy()        # QueryDict → mutable copy
                from nova.tools import get_tool_type

                meta = get_tool_type(data["tool_subtype"])
                if meta:
                    data["name"]        = meta["name"]
                    data["description"] = meta["description"]
                    data["python_path"] = meta["python_path"]
                # Replace the positional arg so ModelForm sees the changes
                args = (data,) + args[1:]

        # ---- 2) Regular ModelForm initialisation -------------------------
        super().__init__(*args, **kwargs)

        # Built-in tools: name / description optional in the form
        self.fields["name"].required        = False
        self.fields["description"].required = False

        # ---- 3) Populate the subtype <select> ----------------------------
        from nova.tools import get_available_tool_types
        tool_types = get_available_tool_types()
        self.fields["tool_subtype"].choices = [("", "---------")] + [
            (key, value["name"]) for key, value in tool_types.items()
        ]

        # ---- 4) Pre-fill JSON editors when editing a non-builtin tool ----
        if self.instance and self.instance.pk:
            if self.instance.input_schema and self.instance.tool_type != Tool.ToolType.BUILTIN:
                self.fields["input_schema"].initial = self.instance.input_schema
            if self.instance.output_schema and self.instance.tool_type != Tool.ToolType.BUILTIN:
                self.fields["output_schema"].initial = self.instance.output_schema

    # --------------------------------------------------------------------- #
    #  Validation helpers                                                   #
    # --------------------------------------------------------------------- #
    def clean(self):
        cleaned_data = super().clean()
        tool_type = cleaned_data.get("tool_type")
        tool_subtype = cleaned_data.get("tool_subtype")

        # Builtin-specific validation
        if tool_type == Tool.ToolType.BUILTIN:
            if not tool_subtype:
                raise forms.ValidationError(_("A BUILTIN tool must have a subtype defined."))

            from nova.tools import get_tool_type
            metadata = get_tool_type(tool_subtype)
            if metadata:
                cleaned_data["name"] = metadata["name"]
                cleaned_data["description"] = metadata["description"]
                cleaned_data["python_path"] = metadata["python_path"]

                cleaned_data["input_schema"] = (
                    cleaned_data.get("input_schema")
                    or metadata.get("input_schema", {})
                )
                cleaned_data["output_schema"] = (
                    cleaned_data.get("output_schema")
                    or metadata.get("output_schema", {})
                )
        else:
            # For API / MCP types, certain fields are required
            if not cleaned_data.get("name"):
                self.add_error("name", _("Name is required for API/MCP tools."))
            if not cleaned_data.get("description"):
                self.add_error("description", _("Description is required for API/MCP tools."))
            if not cleaned_data.get("endpoint"):
                self.add_error("endpoint", _("Endpoint URL is required for API/MCP tools."))

        return cleaned_data

    def save(self, commit: bool = True):
        instance: Tool = super().save(commit=False)

        # Ensure python_path is set for builtin tools
        if (
            instance.tool_type == Tool.ToolType.BUILTIN
            and self.cleaned_data.get("python_path")
        ):
            instance.python_path = self.cleaned_data["python_path"]

        if commit:
            instance.save()
        return instance


# --------------------------------------------------------------------------- #
#  Tool credentials                                                           #
# --------------------------------------------------------------------------- #
class ToolCredentialForm(forms.ModelForm):
    """Handle credentials for tools."""

    # Example of a tool-specific config field
    caldav_url = forms.URLField(
        required=False, help_text=_("CalDav server URL"), assume_scheme='https'
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

    # --------------------------------------------------------------------- #
    #  Constructor                                                          #
    # --------------------------------------------------------------------- #
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.tool: Tool | None = kwargs.pop("tool", None)
        super().__init__(*args, **kwargs)

        # Pre-fill tool-specific config
        if self.instance.pk and self.instance.config:
            if "caldav_url" in self.instance.config:
                self.fields["caldav_url"].initial = self.instance.config.get(
                    "caldav_url"
                )

        # Hide irrelevant auth fields based on auth_type
        auth_type = (
            self.instance.auth_type
            if self.instance.pk
            else self.initial.get("auth_type", "basic")
        )

        def hide(fields: list[str]) -> None:
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

        # Tool-specific requirements
        if self.tool and "CalDav" in self.tool.name:
            self.fields["caldav_url"].required = True
        else:
            self.fields["caldav_url"].widget = forms.HiddenInput()

    # --------------------------------------------------------------------- #
    #  Save                                                                  #
    # --------------------------------------------------------------------- #
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
