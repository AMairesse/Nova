"""Compatibility shim for provider validation now hosted in nova.providers."""

from nova.providers.validation import (
    SOURCE_METADATA,
    SOURCE_PROBE,
    SOURCE_UNKNOWN,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    STATUS_UNSUPPORTED,
    _VALIDATION_IMAGE_BASE64,
    _VALIDATION_PDF_BASE64,
    validate_provider_configuration,
)

__all__ = [
    "SOURCE_METADATA",
    "SOURCE_PROBE",
    "SOURCE_UNKNOWN",
    "STATUS_FAIL",
    "STATUS_NOT_RUN",
    "STATUS_PASS",
    "STATUS_UNSUPPORTED",
    "_VALIDATION_IMAGE_BASE64",
    "_VALIDATION_PDF_BASE64",
    "validate_provider_configuration",
]
