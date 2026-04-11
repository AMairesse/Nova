"""Provider adapter registry and provider-level helper functions."""

from __future__ import annotations

from nova.models.Provider import ProviderType
from nova.providers.base import ProviderDefaults
from nova.providers.llama_cpp import LlamaCppProviderAdapter
from nova.providers.lmstudio import LMStudioProviderAdapter
from nova.providers.mistral import MistralProviderAdapter
from nova.providers.ollama import OllamaProviderAdapter
from nova.providers.openai import OpenAIProviderAdapter
from nova.providers.openrouter import OpenRouterProviderAdapter

_PROVIDER_ADAPTERS = {
    ProviderType.OPENAI: OpenAIProviderAdapter(),
    ProviderType.OPENROUTER: OpenRouterProviderAdapter(),
    ProviderType.MISTRAL: MistralProviderAdapter(),
    ProviderType.OLLAMA: OllamaProviderAdapter(),
    ProviderType.LLAMA_CPP: LlamaCppProviderAdapter(),
    ProviderType.LLMSTUDIO: LMStudioProviderAdapter(),
}


def _resolve_provider_type(provider_or_type):
    if provider_or_type is None:
        return ProviderType.OPENAI
    return getattr(provider_or_type, "provider_type", provider_or_type)


def get_provider_adapter(provider_or_type):
    """Return the adapter registered for the provider or provider type."""
    provider_type = _resolve_provider_type(provider_or_type)
    adapter = _PROVIDER_ADAPTERS.get(provider_type)
    if adapter is None:
        raise ValueError(f"Unsupported provider type: {provider_type}")
    return adapter


def normalize_multimodal_content_for_provider(provider, content):
    """Normalize Nova multimodal blocks to the provider-specific wire format."""
    return get_provider_adapter(provider).normalize_multimodal_content(content)


async def prepare_turn_content_for_provider(provider, intro_text, resolved_inputs, **kwargs):
    """Prepare Nova turn inputs using provider-aware multimodal delivery rules."""
    return await get_provider_adapter(provider).prepare_turn_content(
        provider,
        intro_text,
        resolved_inputs,
        **kwargs,
    )


def get_provider_defaults(provider_or_type) -> ProviderDefaults:
    """Return provider defaults used by forms and runtime."""
    return get_provider_adapter(provider_or_type).get_defaults()


def get_provider_defaults_map() -> dict[str, dict]:
    """Return a JSON-serializable map of defaults keyed by provider type."""
    return {
        str(provider_type): adapter.get_defaults().as_dict()
        for provider_type, adapter in _PROVIDER_ADAPTERS.items()
    }


async def list_provider_models(provider) -> list[dict]:
    return await get_provider_adapter(provider).list_models(provider)


async def resolve_provider_capability_snapshot(provider) -> dict:
    return await get_provider_adapter(provider).resolve_capability_snapshot(provider)


async def complete_provider_chat(provider, *, messages: list[dict], tools: list[dict] | None = None) -> dict:
    return await get_provider_adapter(provider).complete_chat(
        provider,
        messages=messages,
        tools=tools,
    )


async def stream_provider_chat(
    provider,
    *,
    messages: list[dict],
    tools: list[dict] | None = None,
    on_content_delta=None,
) -> dict:
    return await get_provider_adapter(provider).stream_chat(
        provider,
        messages=messages,
        tools=tools,
        on_content_delta=on_content_delta,
    )


async def build_native_provider_request(provider, invocation_request: dict) -> dict:
    return await get_provider_adapter(provider).build_native_request(provider, invocation_request)


async def invoke_native_provider(provider, invocation_request: dict) -> dict:
    return await get_provider_adapter(provider).invoke_native(provider, invocation_request)


async def parse_native_provider_response(provider, raw_response: dict) -> dict:
    return await get_provider_adapter(provider).parse_native_response(provider, raw_response)


def provider_supports_native_response_mode(provider, response_mode: str) -> bool:
    try:
        return bool(
            get_provider_adapter(provider).supports_native_response_mode(
                provider,
                response_mode,
            )
        )
    except Exception:
        return False
