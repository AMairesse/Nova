"""llama.cpp provider adapter."""

from __future__ import annotations

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    OPENAI_COMPATIBLE_LOCAL_HOSTS,
    complete_openai_compatible_chat,
    normalize_openai_compatible_multimodal_content,
    stream_openai_compatible_chat,
)

LLAMA_CPP_ALLOWED_PRIVATE_HOSTS = (*OPENAI_COMPATIBLE_LOCAL_HOSTS, "llamacpp")


class LlamaCppProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=4_096,
                api_key_required=True,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        return await complete_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key or "None",
            base_url=provider.base_url,
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
            allowed_private_hosts=LLAMA_CPP_ALLOWED_PRIVATE_HOSTS,
        )

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        return await stream_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key or "None",
            base_url=provider.base_url,
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
            on_content_delta=on_content_delta,
            allowed_private_hosts=LLAMA_CPP_ALLOWED_PRIVATE_HOSTS,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)
