"""LMStudio provider adapter."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    create_openai_compatible_llm,
    normalize_openai_compatible_multimodal_content,
)


LMSTUDIO_DEFAULT_BASE_URL = "http://localhost:1234/v1"


def get_lmstudio_base_url(base_url: str | None) -> str:
    return (base_url or LMSTUDIO_DEFAULT_BASE_URL).rstrip("/")


def get_lmstudio_models_url(base_url: str | None) -> str:
    parsed = urlsplit(get_lmstudio_base_url(base_url))
    base_path = parsed.path or ""
    if base_path.endswith("/v1"):
        base_path = base_path[: -len("/v1")]
    api_path = f"{base_path.rstrip('/')}/api/v1/models"
    return urlunsplit((parsed.scheme or "http", parsed.netloc, api_path, "", ""))


async def fetch_lmstudio_models(base_url: str | None) -> list[dict]:
    timeout = httpx.Timeout(20.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(get_lmstudio_models_url(base_url))

    if response.status_code >= 400:
        raise RuntimeError(f"LM Studio models request failed with HTTP {response.status_code}.")

    payload = response.json()
    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise RuntimeError("LM Studio models response has an unexpected shape.")
    return [item for item in models if isinstance(item, dict)]


def build_lmstudio_capability_snapshot(model_metadata: dict) -> dict:
    capabilities = model_metadata.get("capabilities") or {}
    loaded_instances = model_metadata.get("loaded_instances")
    max_context_length = model_metadata.get("max_context_length")
    loaded = None
    if isinstance(loaded_instances, list):
        loaded = bool(loaded_instances)

    return {
        "source": "LM Studio models API",
        "model_id": model_metadata.get("id") or model_metadata.get("model_key") or "",
        "input_modalities": {
            "text": "pass",
            "image": "pass" if capabilities.get("vision") is True else "unsupported" if capabilities.get("vision") is False else "unknown",
            "pdf": "unknown",
            "audio": "unknown",
        },
        "output_modalities": {
            "text": "pass",
            "image": "unknown",
            "audio": "unknown",
        },
        "operations": {
            "chat": "pass",
            "streaming": "pass",
            "tools": (
                "pass"
                if capabilities.get("trained_for_tool_use") is True
                else "unsupported"
                if capabilities.get("trained_for_tool_use") is False
                else "unknown"
            ),
            "structured_output": "unknown",
            "reasoning": "unknown",
            "image_generation": "unknown",
            "audio_generation": "unknown",
        },
        "limits": {
            "context_tokens": max_context_length,
        },
        "model_state": {
            "loaded": loaded,
            "loaded_instances": loaded_instances if isinstance(loaded_instances, list) else [],
        },
        "metadata": {
            "vision": capabilities.get("vision"),
            "trained_for_tool_use": capabilities.get("trained_for_tool_use"),
        },
    }


class LMStudioProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_base_url=LMSTUDIO_DEFAULT_BASE_URL,
                default_max_context_tokens=4_096,
                api_key_required=False,
            )
        )

    def create_llm(self, provider):
        return create_openai_compatible_llm(
            model=provider.model,
            api_key="None",
            base_url=get_lmstudio_base_url(provider.base_url),
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    async def list_models(self, provider) -> list[dict]:
        models = await fetch_lmstudio_models(provider.base_url)
        return [
            {
                "id": item.get("id") or item.get("model_key") or "",
                "label": item.get("id") or item.get("model_key") or "",
                "context_length": item.get("max_context_length"),
                "loaded": bool(item.get("loaded_instances")),
            }
            for item in models
            if item.get("id") or item.get("model_key")
        ]

    async def resolve_capability_snapshot(self, provider) -> dict:
        models = await fetch_lmstudio_models(provider.base_url)
        for item in models:
            if item.get("id") == provider.model or item.get("model_key") == provider.model:
                return build_lmstudio_capability_snapshot(item)
        raise RuntimeError(f"Model `{provider.model}` was not found in LM Studio.")
