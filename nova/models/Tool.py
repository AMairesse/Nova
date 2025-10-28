# nova/models/Tool.py
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.tools import get_tool_type
from nova.utils import validate_relaxed_url

logger = logging.getLogger(__name__)

SEARNGX_SERVER_URL = settings.SEARNGX_SERVER_URL
SEARNGX_NUM_RESULTS = settings.SEARNGX_NUM_RESULTS
JUDGE0_SERVER_URL = settings.JUDGE0_SERVER_URL


def get_default_schema():
    return {}


class Tool(models.Model):
    class ToolType(models.TextChoices):
        BUILTIN = "builtin", _("Builtin")
        API = "api", _("API HTTP/REST")
        MCP = "mcp", _("MCP Server")

    class TransportType(models.TextChoices):
        STREAMABLE_HTTP = "streamable_http", _("Streamable HTTP (Default)")
        SSE = "sse", _("SSE (Legacy)")

    # If the Tool is not owned by a user, this will be null
    # it means the Tool is public (available to all users)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             blank=True,
                             null=True,
                             on_delete=models.CASCADE,
                             related_name='tools',
                             verbose_name=_("Tools"))

    name = models.CharField(max_length=120)
    description = models.TextField()

    tool_type = models.CharField(max_length=10,
                                 choices=ToolType.choices,
                                 default=ToolType.BUILTIN)

    # Subtype for BUILTIN tools
    tool_subtype = models.CharField(max_length=50, blank=True, null=True)

    python_path = models.CharField(max_length=255, blank=True)
    endpoint = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        validators=[validate_relaxed_url],
    )

    # Transport type for MCP servers
    transport_type = models.CharField(
        max_length=20,
        choices=TransportType.choices,
        default=TransportType.STREAMABLE_HTTP,
        blank=True,
        help_text=_("Transport method for MCP servers")
    )

    # I/O JSON-Schema contract
    input_schema = models.JSONField(default=get_default_schema,
                                    blank=True, null=True)
    output_schema = models.JSONField(default=get_default_schema,
                                     blank=True, null=True)

    available_functions = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Available functions for this tool, if any.")
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ----- helpers ---------------------------------------------------------
    def clean(self):
        super().clean()
        if self.tool_type == self.ToolType.BUILTIN:
            if not self.tool_subtype:
                raise ValidationError(_("A BUILTIN tool must select a subtype."))

            metadata = get_tool_type(self.tool_subtype)
            if not metadata:
                raise ValidationError(_("Invalid builtin subtype: %s") % self.tool_subtype)

            self.python_path = metadata.get("python_path", "")
            self.input_schema = metadata.get("input_schema", {})
            self.output_schema = metadata.get("output_schema", {})

        if self.tool_type in {self.ToolType.API, self.ToolType.MCP} and not self.endpoint:
            raise ValidationError(_("Endpoint is mandatory for API or MCP tools."))

    def __str__(self):
        return f"{self.name} ({self.tool_type})"


class ToolCredential(models.Model):
    """Store credentials for tools."""

    # If the ToolCredential is not owned by a user, this will be null
    # it means the ToolCredential is linked to a public tool (available to all users)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             null=True,
                             blank=True,
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
    token_type = models.CharField(max_length=50, blank=True, null=True)

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
        return _("{}'s credentials for {}").format(self.user.username,
                                                   self.tool.name)


def check_and_create_searxng_tool():
    # Get the searxng's system tool if it exists
    tool = Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='searxng').first()

    if SEARNGX_SERVER_URL and SEARNGX_NUM_RESULTS:
        # Create a "system tool" if it doesn't already exist
        if not tool:
            tool = Tool.objects.create(user=None,
                                       name='System - SearXNG',
                                       tool_type=Tool.ToolType.BUILTIN,
                                       tool_subtype='searxng',
                                       python_path='nova.tools.builtins.searxng')
            ToolCredential.objects.create(user=None,
                                          tool=tool,
                                          config={'searxng_url': SEARNGX_SERVER_URL,
                                                  'num_results': SEARNGX_NUM_RESULTS})
        else:
            cred = ToolCredential.objects.filter(user=None, tool=tool).first()
            if not cred:
                ToolCredential.objects.create(user=None,
                                              tool=tool,
                                              config={'searxng_url': SEARNGX_SERVER_URL,
                                                      'num_results': SEARNGX_NUM_RESULTS})
            else:
                # Update it if needed
                if cred.config.get('searxng_url') != SEARNGX_SERVER_URL or \
                   cred.config.get('num_results') != SEARNGX_NUM_RESULTS:
                    cred.config['searxng_url'] = SEARNGX_SERVER_URL
                    cred.config['num_results'] = SEARNGX_NUM_RESULTS
                    cred.save()
    else:
        if Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='searxng').exists():
            # If the system tool is not used then delete it
            if not tool.agents.exists():
                tool.delete()
            else:
                logger.warning(
                    """WARNING: SEARXNG_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent.""")


def check_and_create_judge0_tool():
    # Get the judge0's system tool if it exists
    tool = Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='code_execution').first()

    if JUDGE0_SERVER_URL:
        # Create a "system tool" if it doesn't already exist
        if not tool:
            tool = Tool.objects.create(user=None,
                                       name='System - Code Execution',
                                       tool_type=Tool.ToolType.BUILTIN,
                                       tool_subtype='code_execution',
                                       python_path='nova.tools.builtins.code_execution')
            ToolCredential.objects.create(user=None,
                                          tool=tool,
                                          config={'judge0_url': JUDGE0_SERVER_URL})
        else:
            cred = ToolCredential.objects.filter(user=None, tool=tool).first()
            if not cred:
                ToolCredential.objects.create(user=None,
                                              tool=tool,
                                              config={'judge0_url': JUDGE0_SERVER_URL})
            else:
                # Update it if needed
                if cred.config.get('judge0_url') != JUDGE0_SERVER_URL:
                    cred.config['judge0_url'] = JUDGE0_SERVER_URL
                    cred.save()
    else:
        if Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='code_execution').exists():
            # If the system tool is not used then delete it
            if not tool.agents.exists():
                tool.delete()
            else:
                logger.warning(
                    """WARNING: JUDGE0_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent.""")
