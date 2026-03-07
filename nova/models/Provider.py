"""Provider model and active validation helpers."""

import hashlib
import json
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import formats
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.utils import validate_relaxed_url

logger = logging.getLogger(__name__)


VALIDATION_CAPABILITY_ORDER = ("chat", "streaming", "tools", "vision")

VALIDATION_CAPABILITY_LABELS = {
    "chat": _("Chat"),
    "streaming": _("Streaming"),
    "tools": _("Tools"),
    "vision": _("Vision"),
}

CAPABILITY_STATUS_LABELS = {
    "pass": _("Pass"),
    "fail": _("Fail"),
    "unsupported": _("Unsupported"),
    "not_run": _("Not run"),
}

CAPABILITY_STATUS_BADGE_CLASSES = {
    "pass": "text-bg-success",
    "fail": "text-bg-danger",
    "unsupported": "text-bg-warning",
    "not_run": "text-bg-secondary",
}


class ProviderType(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    MISTRAL = "mistral", "Mistral"
    OLLAMA = "ollama", "Ollama"
    LLAMA_CPP = "llama.cpp", "llama.cpp"
    LLMSTUDIO = "lmstudio", "LMStudio"


class LLMProvider(models.Model):
    class ValidationStatus(models.TextChoices):
        UNTESTED = "untested", _("Untested")
        TESTING = "testing", _("Testing")
        VALID = "valid", _("Valid")
        INVALID = "invalid", _("Invalid")
        STALE = "stale", _("Stale")

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
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.UNTESTED,
        db_index=True,
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    validation_summary = models.TextField(blank=True, default="")
    validation_capabilities = models.JSONField(default=dict, blank=True)
    validated_fingerprint = models.CharField(max_length=64, blank=True, default="")
    validation_task_id = models.CharField(max_length=255, blank=True, default="")
    validation_requested_fingerprint = models.CharField(max_length=64, blank=True, default="")

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

    def validation_fingerprint_payload(self) -> dict:
        return {
            "provider_type": (self.provider_type or "").strip(),
            "model": (self.model or "").strip(),
            "base_url": (self.base_url or "").strip(),
            "api_key": self.api_key or "",
            "additional_config": self.additional_config or {},
        }

    def compute_validation_fingerprint(self) -> str:
        payload = json.dumps(
            self.validation_fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def has_validation_snapshot(self) -> bool:
        return bool(
            self.validated_at
            or self.validated_fingerprint
            or self.validation_summary
            or self.validation_capabilities
        )

    @property
    def validation_status_badge_class(self) -> str:
        return {
            self.ValidationStatus.UNTESTED: "text-bg-secondary",
            self.ValidationStatus.TESTING: "text-bg-info",
            self.ValidationStatus.VALID: "text-bg-success",
            self.ValidationStatus.INVALID: "text-bg-danger",
            self.ValidationStatus.STALE: "text-bg-warning",
        }.get(self.validation_status, "text-bg-secondary")

    def get_capability_result(self, capability: str) -> dict:
        if capability not in VALIDATION_CAPABILITY_ORDER:
            return {
                "key": capability,
                "label": capability,
                "status": "not_run",
                "status_label": CAPABILITY_STATUS_LABELS["not_run"],
                "status_badge_class": CAPABILITY_STATUS_BADGE_CLASSES["not_run"],
                "message": "",
                "latency_ms": None,
            }

        raw = {}
        if isinstance(self.validation_capabilities, dict):
            raw = self.validation_capabilities.get(capability) or {}

        status = raw.get("status") or "not_run"
        if status not in CAPABILITY_STATUS_LABELS:
            status = "not_run"

        return {
            "key": capability,
            "label": VALIDATION_CAPABILITY_LABELS[capability],
            "status": status,
            "status_label": CAPABILITY_STATUS_LABELS[status],
            "status_badge_class": CAPABILITY_STATUS_BADGE_CLASSES[status],
            "message": raw.get("message") or "",
            "latency_ms": raw.get("latency_ms"),
        }

    @property
    def validation_capability_items(self) -> list[dict]:
        return [self.get_capability_result(capability) for capability in VALIDATION_CAPABILITY_ORDER]

    def get_known_capability_status(self, capability: str) -> str | None:
        if self.validation_status in {
            self.ValidationStatus.UNTESTED,
            self.ValidationStatus.TESTING,
            self.ValidationStatus.STALE,
        }:
            return None
        return self.get_capability_result(capability)["status"]

    @property
    def known_vision_capability_status(self) -> str:
        return self.get_known_capability_status("vision") or ""

    def is_capability_explicitly_unavailable(self, capability: str) -> bool:
        if self.validation_status != self.ValidationStatus.VALID:
            return False
        return self.get_capability_result(capability)["status"] in {"fail", "unsupported"}

    def build_validation_status_payload(self) -> dict:
        validated_at = self.validated_at
        if validated_at is not None:
            validated_at = timezone.localtime(validated_at)

        return {
            "validation_status": self.validation_status,
            "validation_status_label": self.get_validation_status_display(),
            "validation_status_badge_class": self.validation_status_badge_class,
            "validation_summary": self.validation_summary,
            "validated_at": validated_at.isoformat() if validated_at else None,
            "validated_at_display": (
                formats.date_format(validated_at, "SHORT_DATETIME_FORMAT")
                if validated_at
                else ""
            ),
            "validation_task_id": self.validation_task_id,
            "is_testing": self.validation_status == self.ValidationStatus.TESTING,
            "has_validation_snapshot": self.has_validation_snapshot,
        }

    def apply_validation_result(self, result: dict, *, save: bool = True) -> None:
        self.validation_status = result.get("validation_status") or self.ValidationStatus.INVALID
        self.validation_summary = result.get("validation_summary") or ""
        self.validation_capabilities = result.get("validation_capabilities") or {}
        self.validated_at = result.get("validated_at") or timezone.now()
        self.validated_fingerprint = self.compute_validation_fingerprint()
        self.validation_task_id = ""
        self.validation_requested_fingerprint = ""

        if save:
            self.save(
                update_fields=[
                    "validation_status",
                    "validation_summary",
                    "validation_capabilities",
                    "validated_at",
                    "validated_fingerprint",
                    "validation_task_id",
                    "validation_requested_fingerprint",
                    "updated_at",
                ]
            )

    def mark_validation_started(
        self,
        *,
        task_id: str,
        requested_fingerprint: str | None = None,
        save: bool = True,
    ) -> None:
        self.validation_status = self.ValidationStatus.TESTING
        self.validation_task_id = task_id
        self.validation_requested_fingerprint = requested_fingerprint or self.compute_validation_fingerprint()

        if save:
            self.save(
                update_fields=[
                    "validation_status",
                    "validation_task_id",
                    "validation_requested_fingerprint",
                    "updated_at",
                ]
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields

        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous is not None:
                current_fingerprint = self.compute_validation_fingerprint()
                previous_fingerprint = previous.compute_validation_fingerprint()
                config_changed = current_fingerprint != previous_fingerprint
                has_fresh_validation = (
                    self.validated_fingerprint == current_fingerprint
                    and self.validation_status in {
                        self.ValidationStatus.VALID,
                        self.ValidationStatus.INVALID,
                    }
                )

                if config_changed and not has_fresh_validation:
                    next_status = (
                        self.ValidationStatus.STALE
                        if previous.has_validation_snapshot
                        else self.ValidationStatus.UNTESTED
                    )
                    if self.validation_status != next_status:
                        self.validation_status = next_status
                        if update_fields is not None:
                            update_fields.add("validation_status")
                    if self.validation_task_id or self.validation_requested_fingerprint:
                        self.validation_task_id = ""
                        self.validation_requested_fingerprint = ""
                        if update_fields is not None:
                            update_fields.update(
                                {"validation_task_id", "validation_requested_fingerprint"}
                            )

        super().save(*args, **kwargs)


def check_and_create_system_provider():
    # Define shortcuts for settings
    OLLAMA_SERVER_URL = settings.OLLAMA_SERVER_URL
    OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME
    OLLAMA_CONTEXT_LENGTH = settings.OLLAMA_CONTEXT_LENGTH

    LLAMA_CPP_SERVER_URL = settings.LLAMA_CPP_SERVER_URL
    LLAMA_CPP_MODEL = settings.LLAMA_CPP_MODEL
    LLAMA_CPP_CTX_SIZE = settings.LLAMA_CPP_CTX_SIZE

    # Get the OLLAMA's system provider if it exists
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
            if not provider.AgentsConfig.exists():
                provider.delete()
            else:
                logger.warning(
                    """WARNING: OLLAMA_SERVER_URL or OLLAMA_MODEL_NAME not set, but a system
                       provider exists and is being used by at least one agent.""")
    # Get the LLAMA_CPP's system provider if it exists
    provider = LLMProvider.objects.filter(user=None,
                                          name='System - llama.cpp',
                                          provider_type=ProviderType.LLAMA_CPP).first()
    if LLAMA_CPP_SERVER_URL and LLAMA_CPP_MODEL:
        # Create a "system provider" if it doesn't already exist
        if not provider:
            LLMProvider.objects.create(user=None,
                                       name='System - llama.cpp',
                                       provider_type=ProviderType.LLAMA_CPP,
                                       model=LLAMA_CPP_MODEL,
                                       base_url=LLAMA_CPP_SERVER_URL,
                                       max_context_tokens=LLAMA_CPP_CTX_SIZE)
        else:
            # Update it if needed
            if provider.model != LLAMA_CPP_MODEL or \
               provider.base_url != LLAMA_CPP_SERVER_URL or \
               provider.max_context_tokens != LLAMA_CPP_CTX_SIZE:
                provider.model = LLAMA_CPP_MODEL
                provider.base_url = LLAMA_CPP_SERVER_URL
                provider.max_context_tokens = LLAMA_CPP_CTX_SIZE
                provider.save()
    else:
        existing = LLMProvider.objects.filter(user=None, provider_type=ProviderType.LLAMA_CPP)
        provider = provider or existing.first()
        if provider:
            # If the system provider is not used then delete it
            if not provider.AgentsConfig.exists():
                provider.delete()
            else:
                logger.warning(
                    """WARNING: LLAMA_CPP_SERVER_URL or LLAMA_CPP_MODEL not set, but a system
                       provider exists and is being used by at least one agent.""")
