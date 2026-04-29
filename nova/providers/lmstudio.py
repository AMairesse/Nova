"""LMStudio provider adapter."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    complete_openai_compatible_chat,
    normalize_openai_compatible_multimodal_content,
    stream_openai_compatible_chat,
)
from nova.web.safe_http import safe_http_request


LMSTUDIO_DEFAULT_BASE_URL = "http://localhost:1234/v1"


def _status_from_optional_bool(value: bool | None) -> str:
    if value is True:
        return "pass"
    if value is False:
        return "unsupported"
    return "unknown"


def get_lmstudio_base_url(base_url: str | None) -> str:
    return (base_url or LMSTUDIO_DEFAULT_BASE_URL).rstrip("/")


def get_lmstudio_models_url(base_url: str | None) -> str:
    parsed = urlsplit(get_lmstudio_base_url(base_url))
    base_path = parsed.path or ""
    if base_path.endswith("/v1"):
        base_path = base_path[: -len("/v1")]
    api_path = f"{base_path.rstrip('/')}/api/v1/models"
    return urlunsplit((parsed.scheme or "http", parsed.netloc, api_path, "", ""))


def get_lmstudio_model_identifier(model_metadata: dict) -> str:
    return (
        model_metadata.get("id")
        or model_metadata.get("key")
        or model_metadata.get("model_key")
        or ""
    )


async def fetch_lmstudio_models(base_url: str | None) -> list[dict]:
    timeout = httpx.Timeout(20.0, connect=5.0)
    response = await safe_http_request(
        "GET",
        get_lmstudio_models_url(base_url),
        timeout=timeout,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"LM Studio models request failed with HTTP {response.status_code}.")

    payload = response.json()
    models = payload
    if isinstance(payload, dict):
        models = payload.get("models")
        if models is None:
            models = payload.get("data")
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
        "metadata_source_label": "LM Studio models API",
        "inputs": {
            "text": "pass",
            "image": "pass" if capabilities.get("vision") is True else "unsupported" if capabilities.get("vision") is False else "unknown",
            "pdf": "unknown",
            "audio": "unknown",
        },
        "outputs": {
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
                supports_model_catalog=True,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        return await complete_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key or "None",
            base_url=get_lmstudio_base_url(provider.base_url),
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
        )

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        return await stream_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key or "None",
            base_url=get_lmstudio_base_url(provider.base_url),
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
            on_content_delta=on_content_delta,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    async def list_models(self, provider) -> list[dict]:
        models = await fetch_lmstudio_models(provider.base_url)
        items = []
        for item in models:
            if item.get("type") not in {None, "", "llm"}:
                continue

            model_id = get_lmstudio_model_identifier(item)
            if not model_id:
                continue

            capabilities = item.get("capabilities") or {}
            vision = capabilities.get("vision")
            tool_use = capabilities.get("trained_for_tool_use")
            loaded_instances = item.get("loaded_instances")
            loaded = bool(loaded_instances) if isinstance(loaded_instances, list) else None
            context_length = item.get("max_context_length")

            items.append(
                {
                    "id": model_id,
                    "label": item.get("display_name") or model_id,
                    "description": item.get("description") or "",
                    "context_length": context_length,
                    "suggested_max_context_tokens": context_length,
                    "input_modalities": {
                        "text": "pass",
                        "image": _status_from_optional_bool(vision if isinstance(vision, bool) else None),
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
                        "tools": _status_from_optional_bool(tool_use if isinstance(tool_use, bool) else None),
                        "structured_output": "unknown",
                        "reasoning": "unknown",
                        "image_generation": "unknown",
                        "audio_generation": "unknown",
                    },
                    "pricing": {},
                    "state": {
                        "loaded": loaded,
                        "loaded_instances": loaded_instances if isinstance(loaded_instances, list) else [],
                    },
                    "provider_metadata": {
                        "model_key": item.get("model_key") or item.get("key") or "",
                        "publisher": item.get("publisher") or "",
                        "architecture": item.get("architecture") or item.get("arch") or "",
                        "format": item.get("format") or "",
                        "params_string": item.get("params_string") or "",
                    },
                }
            )

        items.sort(
            key=lambda item: (
                0 if item.get("state", {}).get("loaded") else 1,
                str(item.get("label") or "").lower(),
            )
        )
        return items

    async def resolve_capability_snapshot(self, provider) -> dict:
        models = await fetch_lmstudio_models(provider.base_url)
        for item in models:
            if get_lmstudio_model_identifier(item) == provider.model:
                return build_lmstudio_capability_snapshot(item)
        raise RuntimeError(f"Model `{provider.model}` was not found in LM Studio.")
