"""Base classes and shared types for provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class ProviderDefaults:
    """Serializable provider defaults used by runtime and forms."""

    default_base_url: str = ""
    default_max_context_tokens: int = 4096
    api_key_required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderMetadataError(Exception):
    """Base error for provider metadata lookups."""


class ProviderMetadataAuthError(ProviderMetadataError):
    """Authentication or authorization failed."""


class ProviderModelNotFoundError(ProviderMetadataError):
    """The configured model was not found in the provider catalog."""


class ProviderMetadataTransientError(ProviderMetadataError):
    """Provider metadata lookup failed transiently."""


class BaseProviderAdapter:
    """Contract for provider-specific runtime and validation behavior."""

    metadata_source_label = "Provider metadata"

    def __init__(self, defaults: ProviderDefaults) -> None:
        self._defaults = defaults

    def get_defaults(self) -> ProviderDefaults:
        return self._defaults

    def create_llm(self, provider):
        raise NotImplementedError

    def normalize_multimodal_content(self, content):
        return content

    async def fetch_declared_capabilities(self, provider) -> dict[str, bool | None]:
        return {}
