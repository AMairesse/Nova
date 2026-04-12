"""OpenAI provider adapter."""

from __future__ import annotations

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    complete_openai_compatible_chat,
    normalize_openai_compatible_multimodal_content,
    stream_openai_compatible_chat,
)


class OpenAIProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=100_000,
                api_key_required=True,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        return await complete_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
        )

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        return await stream_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
            on_content_delta=on_content_delta,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return True
