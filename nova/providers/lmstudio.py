"""LMStudio provider adapter."""

from __future__ import annotations

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    create_openai_compatible_llm,
    normalize_openai_compatible_multimodal_content,
)


class LMStudioProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=4_096,
                api_key_required=False,
            )
        )

    def create_llm(self, provider):
        return create_openai_compatible_llm(
            model=provider.model,
            api_key="None",
            base_url=provider.base_url or "http://localhost:1234/v1",
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)
