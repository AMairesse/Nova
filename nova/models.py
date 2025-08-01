# nova/models.py
from importlib import import_module
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField
import json, logging
from datetime import datetime


logger = logging.getLogger(__name__)

class Actor(models.TextChoices):
    USER  = "USR", _("User")
    AGENT = "AGT", _("Agent")

class ProviderType(models.TextChoices):
    OPENAI     = "openai",     "OpenAI"
    MISTRAL    = "mistral",    "Mistral"
    OLLAMA     = "ollama",     "Ollama"
    LLMSTUDIO  = "lmstudio",   "LMStudio"

class LLMProvider(models.Model):
    name = models.CharField(max_length=120)
    provider_type = models.CharField(
        max_length=32,
        choices=ProviderType.choices,
        default=ProviderType.OLLAMA,
    )
    model = models.CharField(max_length=120)  # mistral-small-latest, gpt-4o-mini, llama3, etc.
    api_key = EncryptedCharField(max_length=255, blank=True, null=True)
    base_url = models.URLField(blank=True, null=True)  # For custom endpoints
    additional_config = models.JSONField(default=dict, blank=True)  # For other provider-specific settings
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='llm_providers',
                             verbose_name=_("LLM providers"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = (("user", "name"),)
    
    def __str__(self):
        return f"{self.name} ({self.provider_type})"


class UserParameters(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    allow_langfuse = models.BooleanField(default=False)

    # Langfuse per-user config
    langfuse_public_key = EncryptedCharField(max_length=255, blank=True, null=True)
    langfuse_secret_key = EncryptedCharField(max_length=255, blank=True, null=True)
    langfuse_host = models.URLField(blank=True, null=True)

    def __str__(self):
        return f'Parameters for {self.user.username}'

class Message(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_messages',
                             verbose_name=_("User messages"))
    text = models.TextField()
    actor = models.CharField(max_length=3, choices=Actor.choices)
    thread = models.ForeignKey('Thread', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.text

def get_default_schema():
    return {}

class Tool(models.Model):
    class ToolType(models.TextChoices):
        BUILTIN = "builtin", _("Builtin")
        API     = "api",     _("API HTTP/REST")
        MCP     = "mcp",     _("MCP Server")
        
    user = models.ForeignKey(settings.AUTH_USER_MODEL, 
                             on_delete=models.CASCADE, 
                             related_name='tools',
                             verbose_name=_("Tools"))

    name        = models.CharField(max_length=120)
    description = models.TextField()

    tool_type   = models.CharField(max_length=10,
                                   choices=ToolType.choices,
                                   default=ToolType.BUILTIN)
    
    # Subtype for BUILTIN tools
    tool_subtype = models.CharField(max_length=50, blank=True, null=True)

    python_path = models.CharField(max_length=255, blank=True)  # ex: "my_pkg.utils.search"
    endpoint    = models.URLField(blank=True)                   # ex: "https://weather.xyz/v1"

    # I/O JSON-Schema contract
    input_schema  = models.JSONField(default=get_default_schema, blank=True, null=True)
    output_schema = models.JSONField(default=get_default_schema, blank=True, null=True)

    available_functions = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Available functions for this tool, if any.")
    )

    is_active   = models.BooleanField(default=True)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    # ----- helpers ---------------------------------------------------------
    def clean(self):
        super().clean()
        if self.tool_type == self.ToolType.BUILTIN:
            if not self.tool_subtype:
                raise ValidationError(_("A BUILTIN tool must select a subtype."))
            
            from nova.tools import get_tool_type, get_available_functions, get_metadata
            metadata = get_tool_type(self.tool_subtype)
            if not metadata:
                raise ValidationError(_("Invalid builtin subtype: %s") % self.tool_subtype)
            
            self.python_path = metadata.get("python_path", "")
            self.input_schema = metadata.get("input_schema", {})
            self.output_schema = metadata.get("output_schema", {})
            
            # Optional: Validate functions exist
            if not get_available_functions(self.python_path):
                logger.warning("No functions found for %s", self.python_path)
        
        if self.tool_type in {self.ToolType.API, self.ToolType.MCP} and not self.endpoint:
            raise ValidationError(_("Endpoint is mandatory for API or MCP tools."))

    def __str__(self):
        return f"{self.name} ({self.tool_type})"

class Agent(models.Model):
    user              = models.ForeignKey(settings.AUTH_USER_MODEL,
                                          on_delete=models.CASCADE,
                                          related_name='user_agents',
                                          verbose_name=_("User agents"))
    name              = models.CharField(max_length=120)
    llm_provider      = models.ForeignKey(LLMProvider,
                                          on_delete=models.CASCADE,
                                          related_name='agents',
                                          verbose_name=_("Agents"))
    system_prompt     = models.TextField()

    # Tools
    tools   = models.ManyToManyField(Tool, blank=True, related_name="agents", verbose_name=_("Agents"))
    is_tool = models.BooleanField(
        default=False,
        help_text=_("If true, this agent can be used as a tool by other agents.")
    )

    # Agents as tools
    agent_tools = models.ManyToManyField(
        'self', 
        blank=True, 
        symmetrical=False,
        related_name='used_by_agents',
        verbose_name=_("Used by agents"),
        limit_choices_to={'is_tool': True}
    )

    # New field for tool description
    tool_description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Description of this agent when used as a tool (required if is_tool=True)")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ----- helpers ---------------------------------------------------------
    class Meta:
        unique_together = (("user", "name"),)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_tool and not self.tool_description:
            raise ValidationError(
                _("Tool description is required when 'is_tool' is True.")
            )

        # Skip cycle-detection on instances without PK
        if self._state.adding:        # object not yet in DB
            return
        
        # Check for cycles
        if self._has_cycle():
            raise ValidationError(_("Found a cyclic dependency between agents."))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        
        # Auto-set as default if first normal agent (not tool)
        if not self.is_tool:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            if not profile.default_agent:
                profile.default_agent = self
                profile.save()

    # -----------------------------------------------------------------
    # Internal cycle detector (DFS with recursion stack)
    # -----------------------------------------------------------------
    def _has_cycle(self, visited=None, stack=None):
        """
        Return True if a dependency cycle is found starting from `self`.
        """
        visited = visited or set()
        stack   = stack   or set()

        if self in stack:          # back-edge ⇒ cycle
            return True
        if self in visited:        # already explored, no cycle via this node
            return False

        visited.add(self)
        stack.add(self)

        for dep in self.agent_tools.all():
            if dep._has_cycle(visited, stack):
                return True

        stack.remove(self)
        return False

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    default_agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL)


class ToolCredential(models.Model):
    """Store credentials for tools."""
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='tool_credentials',
                             verbose_name=_("Tool credentials"))
    tool = models.ForeignKey(Tool,
                             on_delete=models.CASCADE,
                             related_name='credentials',
                             verbose_name=_("Credentials"))
    
    auth_type = models.CharField(
        max_length=20,
        choices=[
            ('none', _('No Authentication')),
            ('basic', _('Basic Auth')),
            ('token', _('Token Auth')),
            ('oauth', _('OAuth')),
            ('api_key', _('API Key')),
            ('custom', _('Custom')),
        ],
        default='basic'
    )
    
    # Basic Auth fields
    username = models.CharField(max_length=255, blank=True, null=True)
    password = EncryptedCharField(max_length=255, blank=True, null=True)
    
    # Token/API Key fields
    token = EncryptedCharField(max_length=512, blank=True, null=True)
    token_type = models.CharField(max_length=50, blank=True, null=True)  # ex: "Bearer", "Basic", etc.
    
    # OAuth fields
    client_id = models.CharField(max_length=255, blank=True, null=True)
    client_secret = EncryptedCharField(max_length=255, blank=True, null=True)
    refresh_token = EncryptedCharField(max_length=255, blank=True, null=True)
    access_token = EncryptedCharField(max_length=255, blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    
    # Additional config
    config = models.JSONField(default=dict, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'tool')
    
    def __str__(self):
        return _("{}'s credentials for {}").format(self.user.username, self.tool.name)


class Thread(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_threads',
                             verbose_name=_("User threads"))
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.subject

    def add_message(self, message_text, actor):
        message = Message(text=message_text, thread=self)
        message.user = self.user
        if actor not in Actor.values:
            raise ValueError(_("Invalid actor: {}").format(actor))
        message.actor = actor
        message.save()
        return message
    
    def get_messages(self):
        return Message.objects.filter(thread=self, user=self.user)

# ----- Task Model for Asynchronous AI Tasks -----
class TaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")

class Task(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='tasks',
                             verbose_name=_("User tasks"))
    thread = models.ForeignKey('Thread',
                               on_delete=models.CASCADE,
                               related_name='tasks',
                               verbose_name=_("Thread"))
    agent = models.ForeignKey('Agent',
                              on_delete=models.SET_NULL,
                              null=True,
                              blank=True,
                              related_name='tasks',
                              verbose_name=_("Agent"))
    status = models.CharField(max_length=10,
                              choices=TaskStatus.choices,
                              default=TaskStatus.PENDING)
    progress_logs = models.JSONField(default=list, blank=True)  # List of dicts, e.g., [{"step": "Calling tool X", "timestamp": "2025-07-28T03:58:00Z"}]
    result = models.TextField(blank=True, null=True)  # Final output or error message
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task {self.id} for Thread {self.thread.subject} ({self.status})"
