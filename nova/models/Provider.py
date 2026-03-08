"""Provider model and active verification helpers."""

import hashlib
import json
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import formats
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField

from nova.provider_capability_profile import (
    CAPABILITY_PROFILE_SCHEMA_VERSION,
    CAPABILITY_EFFECTIVE_STATUS_BADGE_CLASSES,
    CAPABILITY_EFFECTIVE_STATUS_LABELS,
    CAPABILITY_PROFILE_GROUPS,
    CAPABILITY_PROFILE_LABELS,
    CAPABILITY_SOURCE_LABELS,
    PROBED_OPERATION_KEYS,
    build_capability_profile_summary,
    ensure_capability_profile,
    merge_declared_capabilities,
    merge_verified_operations,
)
from nova.utils import validate_relaxed_url

logger = logging.getLogger(__name__)


VALIDATION_CAPABILITY_ORDER = PROBED_OPERATION_KEYS


class ProviderType(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    OPENROUTER = "openrouter", "OpenRouter"
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
    model = models.CharField(max_length=120, blank=True, default="")
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
    validated_fingerprint = models.CharField(max_length=64, blank=True, default="")
    validation_task_id = models.CharField(max_length=255, blank=True, default="")
    validation_requested_fingerprint = models.CharField(max_length=64, blank=True, default="")
    capability_profile = models.JSONField(default=dict, blank=True)

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
    def has_model_configured(self) -> bool:
        return bool((self.model or "").strip())

    @property
    def has_verification_result(self) -> bool:
        return bool(self.probe_checked_at or self.validated_fingerprint)

    @property
    def has_capability_profile(self) -> bool:
        return bool(self.capability_profile)

    def get_capability_profile(self) -> dict:
        raw_profile = self.capability_profile if isinstance(self.capability_profile, dict) else {}
        if not raw_profile:
            return {}
        return ensure_capability_profile(raw_profile)

    @property
    def capability_profile_fingerprint(self) -> str:
        profile = self.get_capability_profile()
        return str(profile.get("fingerprint") or "") if profile else ""

    @property
    def has_current_capability_profile(self) -> bool:
        if not self.has_capability_profile:
            return False
        return (
            self.capability_profile_fingerprint == self.compute_validation_fingerprint()
            and self.capability_profile_schema_version == CAPABILITY_PROFILE_SCHEMA_VERSION
        )

    @property
    def capability_profile_schema_version(self) -> int | None:
        profile = self.get_capability_profile()
        if not profile:
            return None
        schema_version = profile.get("schema_version")
        if isinstance(schema_version, int) and schema_version > 0:
            return schema_version
        return None

    def _get_profile_datetime(self, key: str):
        profile = self.get_capability_profile()
        if not profile or not self.has_current_capability_profile:
            return None
        value = profile.get(key)
        if not isinstance(value, str) or not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return timezone.localtime(parsed)

    @property
    def metadata_checked_at(self):
        return self._get_profile_datetime("metadata_checked_at")

    @property
    def probe_checked_at(self):
        return self._get_profile_datetime("probe_checked_at")

    @property
    def validation_status_badge_class(self) -> str:
        return {
            self.ValidationStatus.UNTESTED: "text-bg-secondary",
            self.ValidationStatus.TESTING: "text-bg-info",
            self.ValidationStatus.VALID: "text-bg-success",
            self.ValidationStatus.INVALID: "text-bg-danger",
            self.ValidationStatus.STALE: "text-bg-warning",
        }.get(self.validation_status, "text-bg-secondary")

    @property
    def verification_status_badge_class(self) -> str:
        return self.validation_status_badge_class

    @property
    def verification_status_label(self):
        return {
            self.ValidationStatus.UNTESTED: _("Untested"),
            self.ValidationStatus.TESTING: _("Verifying"),
            self.ValidationStatus.VALID: _("Verified"),
            self.ValidationStatus.INVALID: _("Verification failed"),
            self.ValidationStatus.STALE: _("Stale"),
        }.get(self.validation_status, _("Untested"))

    def _build_capability_item(self, group_name: str, capability_key: str) -> dict:
        label = CAPABILITY_PROFILE_LABELS[group_name][capability_key]
        if not self.has_current_capability_profile:
            return {
                "key": capability_key,
                "label": label,
                "group": group_name,
                "status": "unknown",
                "status_label": CAPABILITY_EFFECTIVE_STATUS_LABELS["unknown"],
                "status_badge_class": CAPABILITY_EFFECTIVE_STATUS_BADGE_CLASSES["unknown"],
                "source": "none",
                "source_label": CAPABILITY_SOURCE_LABELS["none"],
                "message": "",
                "latency_ms": None,
            }

        profile = self.get_capability_profile()
        entry = (
            ((profile.get(group_name) or {}).get(capability_key))
            if isinstance(profile.get(group_name), dict)
            else None
        ) or {}
        status = str(entry.get("effective_status") or "").strip().lower()
        source = str(entry.get("effective_source") or "").strip().lower()

        if status not in CAPABILITY_EFFECTIVE_STATUS_LABELS:
            status = "unknown"
        if source not in CAPABILITY_SOURCE_LABELS:
            source = "none"

        return {
            "key": capability_key,
            "label": label,
            "group": group_name,
            "status": status,
            "status_label": CAPABILITY_EFFECTIVE_STATUS_LABELS[status],
            "status_badge_class": CAPABILITY_EFFECTIVE_STATUS_BADGE_CLASSES[status],
            "source": source,
            "source_label": CAPABILITY_SOURCE_LABELS[source],
            "message": str(entry.get("effective_message") or ""),
            "latency_ms": entry.get("verified_latency_ms"),
        }

    def get_capability_result(self, capability: str) -> dict:
        if capability not in VALIDATION_CAPABILITY_ORDER:
            return {
                "key": capability,
                "label": capability,
                "group": "operations",
                "status": "unknown",
                "status_label": CAPABILITY_EFFECTIVE_STATUS_LABELS["unknown"],
                "status_badge_class": CAPABILITY_EFFECTIVE_STATUS_BADGE_CLASSES["unknown"],
                "source": "none",
                "source_label": CAPABILITY_SOURCE_LABELS["none"],
                "message": "",
                "latency_ms": None,
            }
        return self._build_capability_item("operations", capability)

    def get_effective_capability_status(self, group_name: str, capability_key: str) -> str | None:
        if not self.has_current_capability_profile:
            return None
        profile = self.get_capability_profile()
        entry = (
            ((profile.get(group_name) or {}).get(capability_key))
            if isinstance(profile.get(group_name), dict)
            else None
        ) or {}
        status = str(entry.get("effective_status") or "").strip().lower()
        if status in {"pass", "fail", "unsupported"}:
            return status
        return None

    def get_known_capability_status(self, capability: str) -> str | None:
        return self.get_effective_capability_status("operations", capability)

    @property
    def known_vision_capability_status(self) -> str:
        return self.get_known_capability_status("vision") or ""

    @property
    def known_tools_capability_status(self) -> str:
        return self.get_known_capability_status("tools") or ""

    def is_capability_explicitly_unavailable(self, capability: str) -> bool:
        return self.get_capability_result(capability)["status"] in {"fail", "unsupported"}

    def build_verification_status_payload(self) -> dict:
        probe_checked_at = self.probe_checked_at
        metadata_checked_at = self.metadata_checked_at

        return {
            "verification_status": self.validation_status,
            "verification_status_label": str(self.verification_status_label),
            "verification_status_badge_class": self.verification_status_badge_class,
            "capability_summary": self.capability_profile_summary,
            "probe_checked_at": probe_checked_at.isoformat() if probe_checked_at else None,
            "probe_checked_at_display": (
                formats.date_format(probe_checked_at, "SHORT_DATETIME_FORMAT")
                if probe_checked_at
                else ""
            ),
            "verification_task_id": self.validation_task_id,
            "is_verifying": self.validation_status == self.ValidationStatus.TESTING,
            "has_verification_result": self.has_verification_result,
            "metadata_checked_at": (
                metadata_checked_at.isoformat() if metadata_checked_at else None
            ),
            "metadata_checked_at_display": (
                formats.date_format(metadata_checked_at, "SHORT_DATETIME_FORMAT")
                if metadata_checked_at
                else ""
            ),
        }

    @property
    def capability_input_items(self) -> list[dict]:
        return [
            self._build_capability_item("inputs", capability_key)
            for capability_key in CAPABILITY_PROFILE_GROUPS["inputs"]
        ]

    @property
    def capability_output_items(self) -> list[dict]:
        return [
            self._build_capability_item("outputs", capability_key)
            for capability_key in CAPABILITY_PROFILE_GROUPS["outputs"]
        ]

    @property
    def capability_operation_items(self) -> list[dict]:
        return [
            self._build_capability_item("operations", capability_key)
            for capability_key in CAPABILITY_PROFILE_GROUPS["operations"]
        ]

    def get_known_snapshot_status(self, section_name: str, key: str) -> str | None:
        return self.get_effective_capability_status(section_name, key)

    def is_input_modality_explicitly_unavailable(self, modality: str) -> bool:
        return self.get_known_snapshot_status("inputs", modality) == "unsupported"

    @property
    def known_image_input_status(self) -> str:
        return self.get_known_snapshot_status("inputs", "image") or ""

    @property
    def known_pdf_input_status(self) -> str:
        return self.get_known_snapshot_status("inputs", "pdf") or ""

    @property
    def known_audio_input_status(self) -> str:
        return self.get_known_snapshot_status("inputs", "audio") or ""

    @property
    def known_image_output_status(self) -> str:
        return self.get_known_snapshot_status("outputs", "image") or ""

    @property
    def known_audio_output_status(self) -> str:
        return self.get_known_snapshot_status("outputs", "audio") or ""

    @property
    def capability_profile_summary(self) -> str:
        if not self.has_current_capability_profile:
            return ""
        profile = self.get_capability_profile()
        return str(profile.get("summary") or "")

    def apply_declared_capabilities(self, declared_fragment: dict | None, *, save: bool = True) -> None:
        profile = merge_declared_capabilities(
            self.capability_profile,
            declared_fragment,
            fingerprint=self.compute_validation_fingerprint(),
            checked_at_iso=timezone.now().isoformat(),
        )
        self.capability_profile = profile
        if save:
            self.save(update_fields=["capability_profile", "updated_at"])

    def apply_verification_result(self, result: dict, *, save: bool = True) -> None:
        summary_override = str(result.get("verification_summary") or "")
        profile = merge_verified_operations(
            self.capability_profile,
            result.get("verified_operations") or {},
            fingerprint=self.compute_validation_fingerprint(),
            checked_at_iso=timezone.now().isoformat(),
        )
        if summary_override and not profile.get("metadata_checked_at"):
            profile["summary"] = summary_override
        self.capability_profile = profile
        self.validation_status = result.get("validation_status") or self.ValidationStatus.INVALID
        self.validated_fingerprint = self.compute_validation_fingerprint()
        self.validation_task_id = ""
        self.validation_requested_fingerprint = ""

        if save:
            self.save(
                update_fields=[
                    "capability_profile",
                    "validation_status",
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
                        if previous.has_verification_result
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
                if config_changed and previous.capability_profile:
                    self.capability_profile = {}
                    if update_fields is not None:
                        update_fields.add("capability_profile")

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
