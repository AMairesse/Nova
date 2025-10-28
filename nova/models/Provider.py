# nova/models/provider.py
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.utils import validate_relaxed_url

logger = logging.getLogger(__name__)

OLLAMA_SERVER_URL = settings.OLLAMA_SERVER_URL
OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME
OLLAMA_CONTEXT_LENGTH = settings.OLLAMA_CONTEXT_LENGTH


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


def check_and_create_system_provider():
    # Get the system provider if it exists
    provider = LLMProvider.objects.filter(user=None,
                                          name='System - Ollama',
                                          provider_type=ProviderType.OLLAMA).first()
    if OLLAMA_SERVER_URL and OLLAMA_MODEL_NAME:
        # Create a "system provider" if it doesn't already exist
        if not provider:
            LLMProvider.objects.create(user=None,
                                       name='System - Ollama',
                                       provider_type=ProviderType.OLLAMA,
                                       model=OLLAMA_MODEL_NAME,
                                       base_url=OLLAMA_SERVER_URL,
                                       max_context_tokens=OLLAMA_CONTEXT_LENGTH)
        else:
            # Update it if needed
            if provider.model != OLLAMA_MODEL_NAME or \
               provider.base_url != OLLAMA_SERVER_URL or \
               provider.max_context_tokens != OLLAMA_CONTEXT_LENGTH:
                provider.model = OLLAMA_MODEL_NAME
                provider.base_url = OLLAMA_SERVER_URL
                provider.max_context_tokens = OLLAMA_CONTEXT_LENGTH
                provider.save()
    else:
        existing = LLMProvider.objects.filter(user=None, provider_type=ProviderType.OLLAMA)
        provider = provider or existing.first()
        if provider:
            # If the system provider is not used then delete it
            if not provider.agents.exists():
                provider.delete()
            else:
                logger.warning(
                    """WARNING: OLLAMA_SERVER_URL or OLLAMA_MODEL_NAME not set, but a system
                       provider exists and is being used by at least one agent.""")
