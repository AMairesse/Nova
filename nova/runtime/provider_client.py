from __future__ import annotations

from typing import Awaitable, Callable

from nova.providers.registry import (
    complete_provider_chat,
    invoke_native_provider,
    parse_native_provider_response,
    provider_supports_native_response_mode,
    stream_provider_chat,
)


class ProviderClient:
    def __init__(self, provider):
        if provider is None:
            raise ValueError("Nova runtime requires an LLM provider.")
        model = str(getattr(provider, "model", "") or "").strip()
        if not model:
            raise ValueError("The selected provider has no model configured.")

        self.provider = provider
        self.model = model

    @property
    def max_context_tokens(self) -> int | None:
        value = getattr(self.provider, "max_context_tokens", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def create_chat_completion(self, *, messages: list[dict], tools: list[dict] | None = None):
        return await complete_provider_chat(
            self.provider,
            messages=messages,
            tools=tools,
        )

    async def stream_chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ):
        return await stream_provider_chat(
            self.provider,
            messages=messages,
            tools=tools,
            on_content_delta=on_content_delta,
        )

    def supports_native_response_mode(self, response_mode: str) -> bool:
        return provider_supports_native_response_mode(self.provider, response_mode)

    async def invoke_native_completion(self, *, invocation_request: dict) -> dict:
        raw_response = await invoke_native_provider(self.provider, invocation_request)
        return await parse_native_provider_response(self.provider, raw_response)
