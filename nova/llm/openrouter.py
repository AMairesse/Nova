"""Compatibility shim for OpenRouter helpers now hosted in nova.providers."""

from nova.providers.openrouter import (
    OPENROUTER_ALLOWED_PATHS,
    OPENROUTER_DEFAULT_BASE_URL,
    OPENROUTER_TOOL_PARAMETERS,
    OpenRouterMetadataAuthError,
    OpenRouterMetadataError,
    OpenRouterMetadataTransientError,
    OpenRouterModelNotFoundError,
    fetch_openrouter_model_metadata,
    get_openrouter_base_url,
    get_openrouter_models_url,
    is_openrouter_base_url,
    parse_openrouter_declared_capabilities,
)

__all__ = [
    "OPENROUTER_ALLOWED_PATHS",
    "OPENROUTER_DEFAULT_BASE_URL",
    "OPENROUTER_TOOL_PARAMETERS",
    "OpenRouterMetadataAuthError",
    "OpenRouterMetadataError",
    "OpenRouterMetadataTransientError",
    "OpenRouterModelNotFoundError",
    "fetch_openrouter_model_metadata",
    "get_openrouter_base_url",
    "get_openrouter_models_url",
    "is_openrouter_base_url",
    "parse_openrouter_declared_capabilities",
]
