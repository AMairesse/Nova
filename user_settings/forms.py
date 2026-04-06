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

from nova.models.APIToolOperation import APIToolOperation
from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider
from nova.providers import get_provider_defaults
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters
from user_settings.mixins import SecretPreserveMixin


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
            if ptype:
                defaults = get_provider_defaults(ptype)
                self.initial.setdefault("max_context_tokens", defaults.default_max_context_tokens)
                if defaults.default_base_url:
                    self.initial.setdefault("base_url", defaults.default_base_url)

        # Make api_key optional for local providers
        ptype_current = (
            self.data.get("provider_type")
            or self.initial.get("provider_type")
            or getattr(self.instance, "provider_type", None)
        )
        if ptype_current:
            existing_api_key = getattr(self, "_existing_secrets", {}).get("api_key")
            self.fields["api_key"].required = (
                get_provider_defaults(ptype_current).api_key_required and not bool(existing_api_key)
            )
        self.fields["model"].required = False

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

    def clean_model(self) -> str:
        return str(self.cleaned_data.get("model") or "").strip()

    def clean(self):
        data = super().clean()
        model = str(data.get("model") or "").strip()
        if not model and self.instance.pk and self.instance.AgentsConfig.exists():
            self.add_error(
                "model",
                _("A model is still required because this provider is currently used by one or more agents."),
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
            "runtime_engine",
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
            ).exclude(model="")
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
        self.fields["runtime_engine"].required = False
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
            "runtime_engine",
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
        self.provider_tool_warning = self._compute_provider_tool_warning()
        self.provider_capability_map = self._build_provider_capability_map()

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

    def _compute_provider_tool_warning(self) -> str:
        provider = self._resolve_selected_provider()
        if not provider or not provider.is_capability_explicitly_unavailable("tools"):
            return ""
        if not self._has_selected_tool_dependencies():
            if not bool(self._bound_or_initial_value("is_tool")):
                return _(
                    "This provider/model was verified without tool support. Simple thread runs can still work in tool-less mode, but this agent will not be usable in continuous mode."
                )
            return ""
        return _(
            "This provider/model was verified without tool support, but this agent currently depends on tools or sub-agents."
        )

    def _resolve_selected_provider(self):
        provider_value = self._bound_or_initial_value("llm_provider")
        try:
            provider_id = int(provider_value)
        except (TypeError, ValueError):
            return getattr(self.instance, "llm_provider", None)
        return self.fields["llm_provider"].queryset.filter(pk=provider_id).first()

    def _bound_or_initial_value(self, field_name: str):
        if self.is_bound:
            if hasattr(self.data, "getlist"):
                values = self.data.getlist(field_name)
                if len(values) > 1:
                    return values
            return self.data.get(field_name)
        if field_name == "tools" and self.instance.pk:
            return list(self.instance.tools.values_list("pk", flat=True))
        if field_name == "agent_tools" and self.instance.pk:
            return list(self.instance.agent_tools.values_list("pk", flat=True))
        return self.initial.get(field_name) or getattr(self.instance, field_name, None)

    def _has_selected_tool_dependencies(self) -> bool:
        if self.is_bound and hasattr(self.data, "getlist"):
            selected_tools = [value for value in self.data.getlist("tools") if str(value).strip()]
            selected_agents = [value for value in self.data.getlist("agent_tools") if str(value).strip()]
            return bool(selected_tools or selected_agents)
        if self.instance.pk:
            return self.instance.has_explicit_tool_dependencies()
        return False

    def _build_provider_capability_map(self) -> dict[str, dict[str, str]]:
        provider_queryset = self.fields["llm_provider"].queryset
        return {
            str(provider.pk): {
                "tools_status": provider.known_tools_capability_status or "",
            }
            for provider in provider_queryset
        }

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

    class Meta:
        model = Tool
        fields = [
            "name",
            "description",
            "tool_type",
            "tool_subtype",
            "endpoint",
            "transport_type",
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

    connection_mode = forms.ChoiceField(
        required=True,
        label=_("Connection mode"),
        help_text=_("Choose how Nova should authenticate to this tool."),
    )

    # Example of a tool-specific config field
    caldav_url = forms.URLField(
        required=False,
        help_text=_("CalDav server URL"),
        empty_value=None,
        assume_scheme='https',
    )
    api_key_name = forms.CharField(
        required=False,
        help_text=_("Header or query parameter name for API key auth"),
    )
    api_key_in = forms.ChoiceField(
        required=False,
        choices=[
            ("header", _("Header")),
            ("query", _("Query parameter")),
        ],
        help_text=_("Where to send the API key when auth type is API Key."),
    )

    _CONNECTION_MODE_REGISTRY = (
        {
            "key": "none",
            "label": _("No Authentication"),
            "description": _("Use this when the remote service accepts anonymous requests."),
            "tool_types": {Tool.ToolType.API, Tool.ToolType.MCP},
        },
        {
            "key": "basic",
            "label": _("Basic Auth"),
            "description": _("Send a username and password with each request."),
            "tool_types": {Tool.ToolType.API, Tool.ToolType.MCP},
        },
        {
            "key": "token",
            "label": _("Access token"),
            "description": _(
                "Send a bearer token manually. Use this too when a service gives you an OAuth access token to paste."
            ),
            "tool_types": {Tool.ToolType.API, Tool.ToolType.MCP},
        },
        {
            "key": "api_key",
            "label": _("API Key"),
            "description": _("Send a static API key in a header or query parameter."),
            "tool_types": {Tool.ToolType.API, Tool.ToolType.MCP},
        },
        {
            "key": "oauth_managed",
            "label": _("Managed OAuth"),
            "description": _("Complete a browser-based OAuth flow and let Nova refresh tokens automatically."),
            "tool_types": {Tool.ToolType.MCP},
        },
    )

    class Meta:
        model = ToolCredential
        fields = [
            "username",
            "password",
            "token",
            "client_id",
            "client_secret",
            "api_key_name",
            "api_key_in",
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
        self._previous_auth_type = str(getattr(self.instance, "auth_type", "") or "").strip().lower()
        self.connection_mode_definitions = self._get_connection_mode_definitions()

        # Crispy helper
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        self.fields["connection_mode"].choices = [
            (mode["key"], mode["label"]) for mode in self.connection_mode_definitions
        ]
        self.fields["token"].label = _("Credential value")
        self.fields["token"].help_text = _(
            "Enter the bearer token, OAuth access token, or API key value for the selected mode."
        )
        self.fields["client_id"].label = _("Client ID")
        self.fields["client_secret"].label = _("Client secret")
        self.fields["client_id"].help_text = _(
            "Optional. Only use this if the remote OAuth provider gave you a pre-registered client ID."
        )
        self.fields["client_secret"].help_text = _(
            "Optional. Only use this if the remote OAuth provider gave you a pre-registered client secret."
        )
        self.initial["connection_mode"] = self._initial_connection_mode()

        # Pre-fill config
        if self.instance.pk and self.instance.config:
            self.fields["caldav_url"].initial = self.instance.config.get(
                "caldav_url"
            )
            self.fields["api_key_name"].initial = self.instance.config.get("api_key_name", "X-API-Key")
            self.fields["api_key_in"].initial = self.instance.config.get("api_key_in", "header")
        else:
            self.fields["api_key_name"].initial = "X-API-Key"
            self.fields["api_key_in"].initial = "header"

        # Add data-auth-field attributes for JS visibility control
        auth_fields = [
            "username",
            "password",
            "token",
            "client_id",
            "client_secret",
            "api_key_name",
            "api_key_in",
        ]
        for field_name in auth_fields:
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.setdefault('data-auth-field', field_name)

        # Note: JS will handle initial visibility based on auth_type

        # Tool-specific requirement
        if self.tool and "CalDav" in self.tool.name:
            self.fields["caldav_url"].required = True
        else:
            self.fields["caldav_url"].widget = forms.HiddenInput()

        self.helper.layout = Layout(
            Div(Field("connection_mode"), css_class="mb-3"),
            Div(Field("username"), css_class="mb-3"),
            Div(Field("password"), css_class="mb-3"),
            Div(Field("token"), css_class="mb-3"),
            Div(Field("api_key_name"), css_class="mb-3"),
            Div(Field("api_key_in"), css_class="mb-3"),
            Div(
                Div(Field("client_id"), css_class="mb-3"),
                Div(Field("client_secret"), css_class="mb-3"),
                css_id="oauthAdvancedFieldsGroup",
                css_class="d-none",
            ),
            Div(Field("caldav_url"), css_class="mb-3"),
        )

    def _get_connection_mode_definitions(self) -> list[dict[str, Any]]:
        if not self.tool:
            return [dict(mode) for mode in self._CONNECTION_MODE_REGISTRY]
        return [
            dict(mode)
            for mode in self._CONNECTION_MODE_REGISTRY
            if self.tool.tool_type in mode["tool_types"]
        ]

    def _initial_connection_mode(self) -> str:
        auth_type = self._previous_auth_type
        if auth_type == "oauth":
            return "token"
        available_modes = {mode["key"] for mode in self.connection_mode_definitions}
        if auth_type in available_modes:
            return auth_type
        return "none"

    def _clear_managed_oauth_state(
        self,
        instance: ToolCredential,
        *,
        clear_client_registration: bool = False,
    ) -> None:
        config = dict(instance.config or {})
        oauth_config = config.get("mcp_oauth")
        if isinstance(oauth_config, dict):
            updated_oauth_config = dict(oauth_config)
            updated_oauth_config["status"] = "disabled"
            updated_oauth_config["last_error"] = ""
            config["mcp_oauth"] = updated_oauth_config
            instance.config = config
        instance.access_token = None
        instance.refresh_token = None
        instance.expires_at = None
        instance.token_type = None
        if clear_client_registration:
            instance.client_id = None
            instance.client_secret = None

    # ------------------------------------------------------------------ #
    #  Save                                                               #
    # ------------------------------------------------------------------ #
    def save(self, commit: bool = True):
        instance: ToolCredential = super().save(commit=False)
        connection_mode = str(self.cleaned_data.get("connection_mode") or "").strip().lower()

        if connection_mode == "oauth_managed":
            instance.auth_type = "oauth_managed"
            instance.username = None
            instance.password = None
            instance.token = None
        else:
            instance.auth_type = connection_mode or "none"
            self._clear_managed_oauth_state(instance, clear_client_registration=True)
            if connection_mode != "basic":
                instance.username = None
                instance.password = None
            if connection_mode not in {"token", "api_key"}:
                instance.token = None

        # Persist tool-specific config
        config = instance.config or {}
        if self.cleaned_data.get("caldav_url"):
            config["caldav_url"] = self.cleaned_data["caldav_url"]
        api_key_name = str(self.cleaned_data.get("api_key_name") or "").strip()
        api_key_in = str(self.cleaned_data.get("api_key_in") or "").strip().lower()
        if connection_mode == "api_key" and api_key_name:
            config["api_key_name"] = api_key_name
        else:
            config.pop("api_key_name", None)
        if connection_mode == "api_key" and api_key_in in {"header", "query"}:
            config["api_key_in"] = api_key_in
        else:
            config.pop("api_key_in", None)
        instance.config = config

        if commit:
            instance.save()
        return instance

    def clean(self):
        cleaned = super().clean()
        connection_mode = str(cleaned.get("connection_mode") or "").strip().lower()
        allowed_modes = {mode["key"] for mode in self.connection_mode_definitions}
        if connection_mode not in allowed_modes:
            self.add_error("connection_mode", _("Choose a valid connection mode."))
            return cleaned

        if connection_mode == "api_key":
            if not str(cleaned.get("token") or "").strip() and not getattr(self.instance, "token", None):
                self.add_error("token", _("This field is required for API key authentication."))
            if not str(cleaned.get("api_key_name") or "").strip():
                self.add_error("api_key_name", _("This field is required for API key authentication."))
            api_key_in = str(cleaned.get("api_key_in") or "").strip().lower()
            if api_key_in not in {"header", "query"}:
                self.add_error("api_key_in", _("Choose where to send the API key."))
        elif connection_mode == "oauth_managed" and (
            not self.tool or self.tool.tool_type != Tool.ToolType.MCP
        ):
            self.add_error("connection_mode", _("Managed OAuth is only available for MCP servers."))
        return cleaned


class APIToolOperationForm(forms.ModelForm):
    query_parameters_csv = forms.CharField(
        required=False,
        help_text=_("Comma-separated list of input field names to send as query parameters."),
    )

    class Meta:
        model = APIToolOperation
        fields = [
            "name",
            "slug",
            "description",
            "http_method",
            "path_template",
            "query_parameters_csv",
            "body_parameter",
            "input_schema",
            "output_schema",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "input_schema": forms.Textarea(attrs={"rows": 6, "class": "json-editor"}),
            "output_schema": forms.Textarea(attrs={"rows": 6, "class": "json-editor"}),
        }

    def __init__(self, *args: Any, user=None, tool: Tool | None = None, **kwargs: Any) -> None:
        self.user = user
        self.tool = tool
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        if self.instance.pk:
            self.fields["query_parameters_csv"].initial = ", ".join(self.instance.query_parameters or [])

    def clean_query_parameters_csv(self) -> list[str]:
        raw = str(self.cleaned_data.get("query_parameters_csv") or "")
        values: list[str] = []
        for chunk in raw.replace("\n", ",").split(","):
            item = chunk.strip()
            if item and item not in values:
                values.append(item)
        return values

    def save(self, commit: bool = True):
        instance: APIToolOperation = super().save(commit=False)
        instance.tool = self.tool or instance.tool
        instance.query_parameters = list(self.cleaned_data.get("query_parameters_csv") or [])
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
            "task_notifications_enabled",
            "api_token_status",
        ]
        widgets = {
            "langfuse_public_key": forms.TextInput(),
            "langfuse_secret_key": forms.PasswordInput(render_value=False),
        }

    # Swallow the extra ``user`` kwarg injected by OwnerFormKwargsMixin
    def __init__(self, *args: Any, user=None, server_state: str = "disabled", **kwargs: Any) -> None:
        self.user = user
        self.server_state = server_state
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

        notifications_field = self.fields["task_notifications_enabled"]
        notifications_field.label = _("Task notifications")
        notifications_field.help_text = _("Send push notifications when tasks complete or fail.")
        if self.server_state != "ready":
            notifications_field.disabled = True
            if self.server_state == "misconfigured":
                notifications_field.help_text = _("Server Web Push configuration is incomplete.")
            else:
                notifications_field.help_text = _("Disabled by the server administrator.")

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
                Field("task_notifications_enabled"),
                css_class="mb-4",
            ),
            Div(
                Field("api_token_status"),
                css_class="mb-3"
            )
        )

    def clean_task_notifications_enabled(self) -> bool:
        if self.server_state != "ready":
            # Preserve existing preference while push is unavailable so unrelated
            # settings updates do not silently opt users out.
            if self.instance and self.instance.pk:
                return bool(self.instance.task_notifications_enabled)
            return False
        return bool(self.cleaned_data.get("task_notifications_enabled"))


# ────────────────────────────────────────────────────────────────────────────
#  Memory embeddings settings (user-level)
# ────────────────────────────────────────────────────────────────────────────
class UserMemoryEmbeddingsForm(SecretPreserveMixin, forms.ModelForm):
    """Configure embeddings provider for long-term memory.

    The user explicitly chooses between the deployment-level system provider,
    a custom provider, or disabling embeddings for memory.
    """

    secret_fields = ("memory_embeddings_api_key",)

    class Meta:
        model = UserParameters
        fields = [
            "memory_embeddings_source",
            "memory_embeddings_url",
            "memory_embeddings_model",
            "memory_embeddings_api_key",
        ]
        widgets = {
            "memory_embeddings_source": forms.RadioSelect,
            "memory_embeddings_api_key": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args: Any, user=None, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

        self.fields["memory_embeddings_source"].choices = [
            (
                MemoryEmbeddingsSource.SYSTEM,
                _("Use system provider"),
            ),
            (
                MemoryEmbeddingsSource.CUSTOM,
                _("Use custom provider"),
            ),
            (
                MemoryEmbeddingsSource.DISABLED,
                _("Disable embeddings"),
            ),
        ]

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Field("memory_embeddings_source"),
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
