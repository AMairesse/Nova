"""Ollama provider adapter."""

from __future__ import annotations

from langchain_ollama.chat_models import ChatOllama

from nova.providers.base import BaseProviderAdapter, ProviderDefaults


class OllamaProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_max_context_tokens=4_096,
                api_key_required=False,
            )
        )

    def create_llm(self, provider):
        return ChatOllama(
            model=provider.model,
            base_url=provider.base_url or "http://localhost:11434",
            temperature=0,
            max_retries=2,
            reasoning=False,
            streaming=True,
        )
