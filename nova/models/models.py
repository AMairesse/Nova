# nova/models/models.py
import re
import uuid
from typing import List
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField
import logging
from datetime import timedelta
import botocore.config
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def validate_relaxed_url(value):
    """
    Simple validator for relaxed URLs:
    This allows single-label hosts like 'langfuse:3000'.
    Checks for scheme (http/https), host, optional port/path.
    """
    if not value:
        return  # Allow empty if blank=True

    # Relaxed regex: scheme://host[:port][/path]
    regex = re.compile(
        r'^(https?://)'  # Scheme (http or https)
        r'([a-z0-9-]+(?:\.[a-z0-9-]+)*|localhost)'  # Host
        r'(?::\d{1,5})?'  # Optional port
        r'(?:/[^\s]*)?$'  # Optional path
    )
    if not regex.match(value):
        raise ValidationError(_("Enter a valid URL."))


class ProviderType(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    MISTRAL = "mistral", "Mistral"
    OLLAMA = "ollama", "Ollama"
    LLMSTUDIO = "lmstudio", "LMStudio"


class LLMProvider(models.Model):
    name = models.CharField(max_length=120)
    provider_type = models.CharField(
        max_length=32,
        choices=ProviderType.choices,
        default=ProviderType.OLLAMA,
    )
    model = models.CharField(max_length=120)
    api_key = EncryptedCharField(max_length=255, blank=True, null=True)
    base_url = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        validators=[validate_relaxed_url],
    )
    # For other provider-specific settings
    additional_config = models.JSONField(default=dict, blank=True)
    max_context_tokens = models.PositiveIntegerField(
        default=4096,
        help_text=_("""Maximum tokens for this provider's context window
                       (e.g., 4096 for small models, 100000 or more for large).""")
    )

    # If the LLMProvider is not owned by a user, this will be null
    # it means the LLMProvider is public (available to all users)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             blank=True,
                             null=True,
                             on_delete=models.CASCADE,
                             related_name='llm_providers',
                             verbose_name=_("LLM providers"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "name"),)

    def __str__(self):
        return f"{self.name} ({self.provider_type})"

    def clean(self):
        super().clean()
        if self.max_context_tokens < 512:
            raise ValidationError(_("Max context tokens must be at least 512."))


class UserParameters(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE)
    allow_langfuse = models.BooleanField(default=False)

    # Langfuse per-user config
    langfuse_public_key = EncryptedCharField(max_length=255, blank=True,
                                             null=True)
    langfuse_secret_key = EncryptedCharField(max_length=255, blank=True,
                                             null=True)
    langfuse_host = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        validators=[validate_relaxed_url],
    )

    # API Token management
    has_api_token = models.BooleanField(
        default=False,
        help_text=_("Whether user has generated an API token")
    )

    def __str__(self):
        return f'Parameters for {self.user.username}'


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

            from nova.tools import get_tool_type
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


class Agent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_agents',
                             verbose_name=_("User agents"))
    name = models.CharField(max_length=120)
    llm_provider = models.ForeignKey(LLMProvider,
                                     on_delete=models.CASCADE,
                                     related_name='agents',
                                     verbose_name=_("Provider"))
    system_prompt = models.TextField(verbose_name=_("Prompt"))
    recursion_limit = models.IntegerField(default=25, verbose_name=_("Recursion limit"))

    # Tools
    tools = models.ManyToManyField(Tool, blank=True, related_name="agents",
                                   verbose_name=_("Tools"))
    is_tool = models.BooleanField(
        default=False,
        verbose_name=_("Is tool"),
        help_text=_("If true, this agent can be used as a tool by other agents.")
    )

    # Agents as tools
    agent_tools = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        related_name='used_by_agents',
        verbose_name=_("Agents to use as tools"),
        limit_choices_to={'is_tool': True}
    )

    # New field for tool description
    tool_description = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Tool description"),
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

        # If agent is a tool, remove from default if it was previously default
        if self.is_tool and self == self.user.userprofile.default_agent:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            profile.default_agent = None
            profile.save()

    # -----------------------------------------------------------------
    # Internal cycle detector (DFS with recursion stack)
    # -----------------------------------------------------------------
    def _has_cycle(self, visited=None, stack=None):
        """
        Return True if a dependency cycle is found starting from `self`.
        """
        visited = visited or set()
        stack = stack or set()

        if self in stack:          # back-edge ==> cycle
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
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE)
    default_agent = models.ForeignKey(Agent, null=True, blank=True,
                                      on_delete=models.SET_NULL)

    # Default agent must be normal agent and belong to the user
    def clean(self):
        super().clean()
        if self.default_agent and self.default_agent.is_tool:
            raise ValidationError(_("Default agent must be a normal agent."))

        if self.default_agent and self.default_agent.user != self.user:
            raise ValidationError(_("Default agent must belong to the user."))


class UserInfo(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE,
                                related_name='user_info')
    markdown_content = models.TextField(
        blank=True,
        default="",
        max_length=50000,
        help_text=_("User information stored in Markdown format")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("User Information")
        verbose_name_plural = _("User Information")

    def __str__(self):
        return f"Info for {self.user.username}"

    def clean(self):
        super().clean()
        # Basic Markdown validation - ensure it starts with # if not empty
        if self.markdown_content and not self.markdown_content.strip().startswith('#'):
            raise ValidationError(_("Markdown content should start with a heading (#)."))

        # Check size limit
        if len(self.markdown_content) > 50000:
            raise ValidationError(_("Content exceeds maximum size of 50,000 characters."))

    def get_themes(self) -> List[str]:
        """Extract theme names from Markdown headings."""
        themes = []
        lines = self.markdown_content.split('\n')
        for line in lines:
            if line.strip().startswith('# '):
                theme = line.strip()[2:].strip()
                if theme:
                    themes.append(theme)

        # Ensure global_user_preferences theme is always present
        if "global_user_preferences" not in themes:
            themes.insert(0, "global_user_preferences")

        return themes


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


# ----- Task Model for Asynchronous AI Tasks -----
class TaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    AWAITING_INPUT = "AWAITING_INPUT", _("Awaiting user input")
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
    status = models.CharField(max_length=20,
                              choices=TaskStatus.choices,
                              default=TaskStatus.PENDING)
    # List of dicts, e.g., [{"step": "Calling tool X", "timestamp": "2025-07-28T03:58:00Z"}]
    progress_logs = models.JSONField(default=list, blank=True)
    # Final output or error message
    result = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task {self.id} for Thread {self.thread.subject} ({self.status})"


class InteractionStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ANSWERED = "ANSWERED", _("Answered")
    CANCELED = "CANCELED", _("Canceled")


class Interaction(models.Model):
    """
    Represents a blocking question asked to the end-user during an agent run.
    Exactly one pending interaction per Task at a given time.
    """
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='interactions',
        verbose_name=_("Task")
    )
    thread = models.ForeignKey(
        'Thread',
        on_delete=models.CASCADE,
        related_name='interactions',
        verbose_name=_("Thread")
    )
    # Optional: the agent (or sub-agent) that asked the question
    agent = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interactions',
        verbose_name=_("Agent")
    )
    # Free-text origin for UI (e.g., "Calendar Agent", "Main Agent")
    origin_name = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name=_("Origin (display name)")
    )

    question = models.TextField(verbose_name=_("Question to user"))
    answer = models.JSONField(blank=True, null=True, default=None, verbose_name=_("User answer"))

    # Optional JSON schema describing expected answer shape
    schema = models.JSONField(default=dict, blank=True, null=True)

    # Payload to store engine-specific resume token/metadata (interrupt handle)
    resume_payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=10,
        choices=InteractionStatus.choices,
        default=InteractionStatus.PENDING
    )

    # Optional expiration / auto-cancel policy (handled at app level later)
    expires_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['task', 'status']),
            models.Index(fields=['thread', 'status']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = _("Interaction")
        verbose_name_plural = _("Interactions")

    def __str__(self):
        origin = self.origin_name or (self.agent.name if self.agent else "Agent")
        return f"Interaction[{self.id}] {origin}: {self.question[:40]}..."

    def clean(self):
        super().clean()
        # Ensure the interaction's thread matches the task thread
        if self.thread_id and self.task_id and self.thread_id != self.task.thread_id:
            raise ValidationError(_("Interaction thread must match task thread."))

        # Enforce single PENDING interaction per task (app-level validation)
        if self.status == InteractionStatus.PENDING and self.task_id:
            qs = Interaction.objects.filter(task_id=self.task_id, status=InteractionStatus.PENDING)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(_("There is already a pending interaction for this task."))


# Model for user-uploaded files stored in MinIO
class UserFile(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='files')
    thread = models.ForeignKey('Thread', on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='files')
    # S3 object key (e.g., users/user_id/threads/thread_id/dir/subdir/file.txt)
    key = models.CharField(max_length=255, unique=True)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    # File size in bytes
    size = models.PositiveIntegerField()
    # Auto-delete after this date
    expiration_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (('user', 'key'),)

    def __str__(self):
        return f"{self.original_filename} ({self.key})"

    def save(self, *args, **kwargs):
        # Generate key only if not set (allow overrides if needed)
        if not self.key and self.user and self.thread:
            self.key = f"users/{self.user.id}/threads/{self.thread.id}{self.original_filename}"

        # Save the object first to set auto_now_add fields
        super().save(*args, **kwargs)

        # Now calculate expiration_date if not already set
        if not self.expiration_date:
            self.expiration_date = self.created_at + timedelta(days=30)
            # Save again with updated field
            super().save(update_fields=['expiration_date'])

    def get_download_url(self, expires_in=3600):
        """Generate presigned URL for download (expires in seconds)."""
        if self.expiration_date and self.expiration_date < timezone.now():
            self.delete()
            raise ValueError("File expired and deleted.")

        # Get external base from trusted origins (includes port like :8080)
        external_base = settings.CSRF_TRUSTED_ORIGINS[0].rstrip('/')

        s3_client = boto3.client(
            's3',
            endpoint_url=settings.MINIO_ENDPOINT_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=botocore.config.Config(
                signature_version='s3v4',
            ),
        )
        try:
            # Generate presigned URL
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': settings.MINIO_BUCKET_NAME, 'Key': self.key},
                ExpiresIn=expires_in
            )

            # Change the URL to include the external base
            url = url.replace(settings.MINIO_ENDPOINT_URL, external_base)

            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

    def delete(self, *args, **kwargs):
        """Delete from DB and MinIO."""
        s3_client = boto3.client(
            's3',
            endpoint_url=settings.MINIO_ENDPOINT_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY
        )
        try:
            s3_client.delete_object(Bucket=settings.MINIO_BUCKET_NAME,
                                    Key=self.key)
        except ClientError as e:
            logger.error(f"Error deleting from MinIO: {e}")
        super().delete(*args, **kwargs)


class CheckpointLink(models.Model):
    # Link to a checkpoint for a given "thread+agent"
    # The langgraph's checkpoint is identified by checkpoint_id
    thread = models.ForeignKey('Thread', on_delete=models.CASCADE,
                               related_name='checkpoint_links')
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE)
    checkpoint_id = models.UUIDField(primary_key=True,
                                     default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('thread', 'agent'),)

    def __str__(self):
        return f"Link to Checkpoint {self.checkpoint_id} for Thread {self.thread.id} and agent {self.agent.id}"
