# nova/models/models.py
import botocore.config
import boto3
import logging
import uuid
from botocore.exceptions import ClientError
from datetime import timedelta
from typing import List

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.models.Tool import Tool
from nova.models.Provider import LLMProvider
from nova.utils import validate_relaxed_url

logger = logging.getLogger(__name__)


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
