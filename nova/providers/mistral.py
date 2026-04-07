"""Mistral provider adapter and metadata helpers."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx
from mistralai.client import Mistral

from nova.providers.base import BaseProviderAdapter, ProviderDefaults
from nova.providers.openai_compatible import (
    build_openai_compatible_messages,
    normalize_openai_compatible_multimodal_content,
    normalize_openai_completion_payload,
)

MISTRAL_DEFAULT_BASE_URL = "https://api.mistral.ai/v1"

_NON_CHAT_MISTRAL_MODEL_MARKERS = (
    "embed",
    "moderation",
    "classif",
    "ocr",
    "tts",
    "transcribe",
)


def _status_from_optional_bool(value: bool | None) -> str:
    if value is True:
        return "pass"
    if value is False:
        return "unsupported"
    return "unknown"


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_mistral_base_url(base_url: str | None) -> str:
    normalized = str(base_url or MISTRAL_DEFAULT_BASE_URL).strip().rstrip("/")
    return normalized or MISTRAL_DEFAULT_BASE_URL


def get_mistral_models_url(base_url: str | None) -> str:
    parsed = urlsplit(get_mistral_base_url(base_url))
    path = parsed.path or ""
    if not path or path == "/":
        path = "/v1"
    return urlunsplit(
        (parsed.scheme or "https", parsed.netloc, f"{path.rstrip('/')}/models", "", "")
    )


def get_mistral_model_identifier(model_metadata: dict) -> str:
    return str(model_metadata.get("id") or model_metadata.get("name") or "").strip()


def get_mistral_model_aliases(model_metadata: dict) -> set[str]:
    aliases = set()
    for candidate in (
        model_metadata.get("aliases"),
        model_metadata.get("alias"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            aliases.add(candidate.strip())
        elif isinstance(candidate, list):
            aliases.update(
                str(item).strip()
                for item in candidate
                if str(item or "").strip()
            )
    return aliases


def mistral_model_matches(model_metadata: dict, configured_model: str) -> bool:
    needle = str(configured_model or "").strip()
    if not needle:
        return False

    candidates = {
        get_mistral_model_identifier(model_metadata),
        str(model_metadata.get("name") or "").strip(),
        str(model_metadata.get("root") or "").strip(),
        *get_mistral_model_aliases(model_metadata),
    }
    return needle in {candidate for candidate in candidates if candidate}


def _get_mistral_capabilities(model_metadata: dict) -> dict:
    capabilities = model_metadata.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else {}


def _is_mistral_chat_model(model_metadata: dict) -> bool:
    capabilities = _get_mistral_capabilities(model_metadata)
    chat_flag = capabilities.get("completion_chat")
    if isinstance(chat_flag, bool):
        return chat_flag

    identifier = " ".join(
        filter(
            None,
            (
                get_mistral_model_identifier(model_metadata),
                str(model_metadata.get("name") or "").strip(),
                str(model_metadata.get("root") or "").strip(),
            ),
        )
    ).lower()
    if not identifier:
        return False
    return not any(marker in identifier for marker in _NON_CHAT_MISTRAL_MODEL_MARKERS)


def _infer_mistral_pdf_status(model_metadata: dict) -> str:
    # Mistral's models API exposes chat/tool/vision flags but no dedicated
    # document capability. Document Q&A is documented on the chat endpoint, so
    # we expose PDF support for chat-capable models and keep non-chat models out.
    return "pass" if _is_mistral_chat_model(model_metadata) else "unsupported"


def normalize_mistral_multimodal_content(content):
    """Translate Nova multimodal blocks to the Mistral chat wire format."""
    normalized = normalize_openai_compatible_multimodal_content(content)
    if not isinstance(normalized, list):
        return normalized

    mistral_content = []
    for part in normalized:
        if not isinstance(part, dict):
            mistral_content.append(part)
            continue

        if part.get("type") == "image_url":
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                url = str(image_url.get("url") or "").strip()
                if url:
                    mistral_content.append(
                        {
                            "type": "image_url",
                            "image_url": url,
                        }
                    )
                    continue

        if part.get("type") == "file":
            file_payload = part.get("file")
            if isinstance(file_payload, dict):
                file_data = str(file_payload.get("file_data") or "").strip()
                if file_data:
                    mistral_content.append(
                        {
                            "type": "document_url",
                            "document_url": file_data,
                        }
                    )
                    continue

        mistral_content.append(part)

    return mistral_content


async def fetch_mistral_model_catalog(api_key: str, base_url: str | None) -> list[dict]:
    if not api_key:
        raise RuntimeError("Mistral metadata lookup failed: missing API key.")

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        try:
            response = await client.get(get_mistral_models_url(base_url))
        except httpx.TimeoutException as exc:
            raise RuntimeError("Mistral model catalog request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Mistral model catalog request failed: {exc}") from exc

    if response.status_code in {401, 403}:
        raise RuntimeError(
            "Mistral metadata lookup failed: invalid API key or unauthorized access."
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Mistral model catalog returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Mistral model catalog returned invalid JSON.") from exc

    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise RuntimeError("Mistral model catalog returned an unexpected payload.")
    return [item for item in models if isinstance(item, dict)]


def build_mistral_catalog_item(model_metadata: dict) -> dict:
    capabilities = _get_mistral_capabilities(model_metadata)
    context_length = _safe_int(
        model_metadata.get("max_context_length") or model_metadata.get("context_length")
    )

    return {
        "id": get_mistral_model_identifier(model_metadata),
        "label": str(model_metadata.get("name") or get_mistral_model_identifier(model_metadata)),
        "description": str(model_metadata.get("description") or ""),
        "context_length": context_length,
        "suggested_max_context_tokens": context_length,
        "input_modalities": {
            "text": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "image": _status_from_optional_bool(
                capabilities.get("vision")
                if isinstance(capabilities.get("vision"), bool)
                else None
            ),
            "pdf": _infer_mistral_pdf_status(model_metadata),
            "audio": "unknown",
        },
        "output_modalities": {
            "text": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "image": "unknown",
            "audio": "unknown",
        },
        "operations": {
            "chat": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "streaming": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "tools": _status_from_optional_bool(
                capabilities.get("function_calling")
                if isinstance(capabilities.get("function_calling"), bool)
                else None
            ),
            "structured_output": "unknown",
            "reasoning": "unknown",
            "image_generation": "unknown",
            "audio_generation": "unknown",
        },
        "pricing": {},
        "state": {},
        "provider_metadata": {
            "root": str(model_metadata.get("root") or ""),
            "owned_by": str(model_metadata.get("owned_by") or ""),
            "aliases": sorted(get_mistral_model_aliases(model_metadata)),
            "capabilities": capabilities,
        },
    }


def build_mistral_capability_snapshot(model_metadata: dict) -> dict:
    capabilities = _get_mistral_capabilities(model_metadata)
    context_length = _safe_int(
        model_metadata.get("max_context_length") or model_metadata.get("context_length")
    )

    return {
        "metadata_source_label": "Mistral models API",
        "inputs": {
            "text": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "image": _status_from_optional_bool(
                capabilities.get("vision")
                if isinstance(capabilities.get("vision"), bool)
                else None
            ),
            "pdf": _infer_mistral_pdf_status(model_metadata),
            "audio": "unknown",
        },
        "outputs": {
            "text": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "image": "unknown",
            "audio": "unknown",
        },
        "operations": {
            "chat": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "streaming": "pass" if _is_mistral_chat_model(model_metadata) else "unsupported",
            "tools": _status_from_optional_bool(
                capabilities.get("function_calling")
                if isinstance(capabilities.get("function_calling"), bool)
                else None
            ),
            "vision": _status_from_optional_bool(
                capabilities.get("vision")
                if isinstance(capabilities.get("vision"), bool)
                else None
            ),
            "structured_output": "unknown",
            "reasoning": "unknown",
            "image_generation": "unknown",
            "audio_generation": "unknown",
        },
        "limits": {
            "context_tokens": context_length,
        },
        "model_state": {},
        "metadata": {
            "root": str(model_metadata.get("root") or ""),
            "owned_by": str(model_metadata.get("owned_by") or ""),
            "aliases": sorted(get_mistral_model_aliases(model_metadata)),
            "capabilities": capabilities,
        },
    }


class MistralProviderAdapter(BaseProviderAdapter):
    metadata_source_label = "Mistral models API"

    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_base_url=MISTRAL_DEFAULT_BASE_URL,
                default_max_context_tokens=100_000,
                api_key_required=True,
                supports_model_catalog=True,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        client = Mistral(
            api_key=provider.api_key,
            server_url=get_mistral_base_url(provider.base_url),
            timeout_ms=60_000,
        )
        response = await client.chat.complete_async(
            model=provider.model,
            messages=build_openai_compatible_messages(
                messages,
                normalize_content=self.normalize_multimodal_content,
            ),
            tools=tools,
            temperature=0,
            tool_choice="auto" if tools else None,
        )
        return normalize_openai_completion_payload(
            response.model_dump(mode="json", exclude_none=True)
        )

    def normalize_multimodal_content(self, content):
        return normalize_mistral_multimodal_content(content)

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return True

    async def list_models(self, provider) -> list[dict]:
        models = await fetch_mistral_model_catalog(provider.api_key or "", provider.base_url)
        items = [
            build_mistral_catalog_item(item)
            for item in models
            if get_mistral_model_identifier(item) and _is_mistral_chat_model(item)
        ]
        items.sort(key=lambda item: str(item.get("label") or "").lower())
        return items

    async def resolve_capability_snapshot(self, provider) -> dict:
        models = await fetch_mistral_model_catalog(provider.api_key or "", provider.base_url)
        for item in models:
            if mistral_model_matches(item, provider.model):
                return build_mistral_capability_snapshot(item)
        raise RuntimeError(f"Model `{provider.model}` was not found in the Mistral catalog.")

    async def fetch_declared_capabilities(self, provider) -> dict[str, bool | None]:
        models = await fetch_mistral_model_catalog(provider.api_key or "", provider.base_url)
        for item in models:
            if not mistral_model_matches(item, provider.model):
                continue
            capabilities = _get_mistral_capabilities(item)
            tools_value = capabilities.get("function_calling")
            vision_value = capabilities.get("vision")
            pdf_supported = _infer_mistral_pdf_status(item) == "pass"
            return {
                "tools": tools_value if isinstance(tools_value, bool) else None,
                "vision": vision_value if isinstance(vision_value, bool) else None,
                "pdf": pdf_supported,
            }
        return {}
