"""OpenAI provider adapter."""

from __future__ import annotations

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    create_openai_compatible_llm,
    normalize_openai_compatible_multimodal_content,
)


class OpenAIProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=100_000,
                api_key_required=True,
            )
        )

    def create_llm(self, provider):
        return create_openai_compatible_llm(
            model=provider.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return True
