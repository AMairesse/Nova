"""Mistral provider adapter."""

from __future__ import annotations

from langchain_mistralai.chat_models import ChatMistralAI

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import normalize_openai_compatible_multimodal_content


class MistralProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=100_000,
                api_key_required=True,
            )
        )

    def create_llm(self, provider):
        return ChatMistralAI(
            model=provider.model,
            mistral_api_key=provider.api_key,
            temperature=0,
            max_retries=2,
            streaming=True,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)
