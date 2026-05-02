"""llama.cpp provider adapter."""

from __future__ import annotations

from django.conf import settings

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    complete_openai_compatible_chat,
    normalize_openai_compatible_multimodal_content,
    stream_openai_compatible_chat,
)
from nova.web.network_policy import build_allowed_private_hosts


def get_llama_cpp_allowed_private_hosts() -> tuple[str, ...]:
    return build_allowed_private_hosts(
        urls=(getattr(settings, "LLAMA_CPP_SERVER_URL", None),),
        hostnames=("llamacpp",),
        include_local_development_hosts=True,
    )


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
            allowed_private_hosts=get_llama_cpp_allowed_private_hosts(),
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
            allowed_private_hosts=get_llama_cpp_allowed_private_hosts(),
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)
