# nova/models/UserObjects.py
from typing import List

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.utils import validate_relaxed_url


class UserInfo(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE,
                                related_name='user_info')
    markdown_content = models.TextField(
        blank=True,
        default="# global_user_preferences\n",
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

        # Check that the global_user_preferences theme as not been deleted
        if self.markdown_content.strip().find('# global_user_preferences') == -1:
            raise ValidationError(_("The 'global_user_preferences' theme cannot be deleted as it is required."))

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


class UserParameters(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE)
    allow_langfuse = models.BooleanField(default=False)

    # ------------------------------------------------------------------
    # Memory embeddings (optional)
    # ------------------------------------------------------------------
    # Note: memory is global per-user, so config lives at user level.
    memory_embeddings_enabled = models.BooleanField(
        default=False,
        help_text=_("Enable semantic (embedding) search for long-term memory"),
    )
    memory_embeddings_url = models.CharField(
        max_length=400,
        blank=True,
        default="",
        help_text=_("Embeddings endpoint URL (OpenAI-compatible /v1 recommended)"),
    )
    memory_embeddings_model = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text=_("Embeddings model name"),
    )
    memory_embeddings_api_key = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("API key for the embeddings endpoint (optional)"),
    )

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


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE)
    default_agent = models.ForeignKey('AgentConfig', null=True, blank=True,
                                      on_delete=models.SET_NULL)

    # Default agent must be normal agent and belong to the user
    def clean(self):
        super().clean()
        if self.default_agent and self.default_agent.is_tool:
            raise ValidationError(_("Default agent must be a normal agent."))

        if self.default_agent and self.default_agent.user != self.user:
            raise ValidationError(_("Default agent must belong to the user."))
