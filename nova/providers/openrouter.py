"""OpenRouter provider adapter and metadata helpers."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from nova.providers.base import (
    BaseProviderAdapter,
    ProviderDefaults,
    ProviderMetadataAuthError,
    ProviderMetadataError,
    ProviderMetadataTransientError,
    ProviderModelNotFoundError,
)
from nova.providers.openai_compatible import (
    complete_openai_compatible_chat,
    normalize_openai_compatible_multimodal_content,
    stream_openai_compatible_chat,
)

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_ALLOWED_PATHS = {"", "/", "/api", "/api/", "/api/v1", "/api/v1/"}
OPENROUTER_TOOL_PARAMETERS = {"tools", "tool_choice", "parallel_tool_calls"}
OPENROUTER_STRUCTURED_OUTPUT_PARAMETERS = {"response_format", "structured_outputs"}


class OpenRouterMetadataError(ProviderMetadataError):
    """Base error for OpenRouter metadata lookups."""


class OpenRouterMetadataAuthError(ProviderMetadataAuthError, OpenRouterMetadataError):
    """Authentication or authorization failed."""


class OpenRouterModelNotFoundError(ProviderModelNotFoundError, OpenRouterMetadataError):
    """The configured model was not found in the OpenRouter catalog."""


class OpenRouterMetadataTransientError(ProviderMetadataTransientError, OpenRouterMetadataError):
    """OpenRouter metadata lookup failed transiently."""


def is_openrouter_base_url(base_url: str | None) -> bool:
    """Return True when the URL points to the canonical OpenRouter API host."""
    if not base_url:
        return False

    parsed = urlsplit(base_url.strip())
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/") or "/"
    normalized_path = path if path == "/" else f"{path}/" if path in {"/api", "/api/v1"} else path

    return hostname == "openrouter.ai" and normalized_path in OPENROUTER_ALLOWED_PATHS


def get_openrouter_base_url(base_url: str | None) -> str:
    """Return the normalized OpenRouter API base URL."""
    if not base_url or not base_url.strip():
        return OPENROUTER_DEFAULT_BASE_URL

    normalized = base_url.strip().rstrip("/")
    if is_openrouter_base_url(normalized):
        parsed = urlsplit(normalized)
        return urlunsplit((parsed.scheme or "https", parsed.netloc, "/api/v1", "", ""))
    return normalized


def get_openrouter_models_url(base_url: str | None) -> str:
    """Return the OpenRouter models catalog endpoint."""
    return f"{get_openrouter_base_url(base_url).rstrip('/')}/models"


def parse_openrouter_declared_capabilities(model_metadata: dict) -> dict:
    """Extract declared OpenRouter capabilities from a model metadata document."""
    architecture = model_metadata.get("architecture") or {}
    input_modalities_raw = architecture.get("input_modalities")
    if input_modalities_raw is None:
        input_modalities_raw = model_metadata.get("input_modalities")

    supported_parameters_raw = model_metadata.get("supported_parameters")

    input_modalities = None
    if isinstance(input_modalities_raw, list):
        input_modalities = {
            str(modality).strip().lower()
            for modality in input_modalities_raw
            if modality
        }

    supported_parameters = None
    if isinstance(supported_parameters_raw, list):
        supported_parameters = {
            str(parameter).strip().lower()
            for parameter in supported_parameters_raw
            if parameter
        }

    return {
        "vision": None if input_modalities is None else "image" in input_modalities,
        "tools": (
            None
            if supported_parameters is None
            else bool(supported_parameters.intersection(OPENROUTER_TOOL_PARAMETERS))
        ),
    }


def _normalize_snapshot_flag(value: bool | None) -> str:
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


def build_openrouter_catalog_item(model_metadata: dict) -> dict:
    architecture = model_metadata.get("architecture") or {}
    top_provider = model_metadata.get("top_provider") or {}

    input_modalities_raw = architecture.get("input_modalities")
    if input_modalities_raw is None:
        input_modalities_raw = model_metadata.get("input_modalities")

    output_modalities_raw = architecture.get("output_modalities")
    if output_modalities_raw is None:
        output_modalities_raw = model_metadata.get("output_modalities")

    supported_parameters_raw = model_metadata.get("supported_parameters")

    input_modalities = {
        str(modality).strip().lower()
        for modality in input_modalities_raw or []
        if modality
    }
    output_modalities = {
        str(modality).strip().lower()
        for modality in output_modalities_raw or []
        if modality
    }
    supported_parameters = {
        str(parameter).strip().lower()
        for parameter in supported_parameters_raw or []
        if parameter
    }

    suggested_context = _safe_int(top_provider.get("context_length")) or _safe_int(model_metadata.get("context_length"))

    pricing = model_metadata.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}

    supports_file_input = None if not input_modalities else ("file" in input_modalities or "pdf" in input_modalities)

    return {
        "id": model_metadata.get("id") or "",
        "label": model_metadata.get("name") or model_metadata.get("id") or "",
        "description": model_metadata.get("description") or "",
        "context_length": _safe_int(model_metadata.get("context_length")),
        "suggested_max_context_tokens": suggested_context,
        "input_modalities": {
            "text": "pass",
            "image": _normalize_snapshot_flag("image" in input_modalities if input_modalities else None),
            "pdf": _normalize_snapshot_flag(supports_file_input),
            "audio": _normalize_snapshot_flag("audio" in input_modalities if input_modalities else None),
        },
        "output_modalities": {
            "text": "pass",
            "image": _normalize_snapshot_flag("image" in output_modalities if output_modalities else None),
            "audio": _normalize_snapshot_flag("audio" in output_modalities if output_modalities else None),
        },
        "operations": {
            "chat": "pass",
            "streaming": "pass",
            "tools": _normalize_snapshot_flag(
                bool(supported_parameters.intersection(OPENROUTER_TOOL_PARAMETERS))
                if supported_parameters
                else None
            ),
            "structured_output": _normalize_snapshot_flag(
                bool(supported_parameters.intersection(OPENROUTER_STRUCTURED_OUTPUT_PARAMETERS))
                if supported_parameters
                else None
            ),
            "reasoning": _normalize_snapshot_flag(
                "reasoning" in supported_parameters if supported_parameters else None
            ),
            "image_generation": _normalize_snapshot_flag(
                "image" in output_modalities if output_modalities else None
            ),
            "audio_generation": _normalize_snapshot_flag(
                "audio" in output_modalities if output_modalities else None
            ),
        },
        "pricing": {
            key: pricing.get(key)
            for key in (
                "prompt",
                "completion",
                "request",
                "image",
                "input_cache_read",
                "input_cache_write",
                "web_search",
            )
            if pricing.get(key) not in {None, ""}
        },
        "state": {},
        "provider_metadata": {
            "canonical_slug": model_metadata.get("canonical_slug") or "",
            "architecture": architecture,
            "top_provider": top_provider,
        },
    }


def build_openrouter_capability_snapshot(model_metadata: dict) -> dict:
    architecture = model_metadata.get("architecture") or {}
    input_modalities_raw = architecture.get("input_modalities")
    if input_modalities_raw is None:
        input_modalities_raw = model_metadata.get("input_modalities")

    output_modalities_raw = architecture.get("output_modalities")
    if output_modalities_raw is None:
        output_modalities_raw = model_metadata.get("output_modalities")

    supported_parameters_raw = model_metadata.get("supported_parameters")

    input_modalities = {
        str(modality).strip().lower()
        for modality in input_modalities_raw or []
        if modality
    }
    output_modalities = {
        str(modality).strip().lower()
        for modality in output_modalities_raw or []
        if modality
    }
    supported_parameters = {
        str(parameter).strip().lower()
        for parameter in supported_parameters_raw or []
        if parameter
    }

    supports_file_input = None if not input_modalities else ("file" in input_modalities or "pdf" in input_modalities)

    return {
        "metadata_source_label": "OpenRouter models API",
        "inputs": {
            "text": "pass",
            "image": _normalize_snapshot_flag("image" in input_modalities if input_modalities else None),
            "pdf": _normalize_snapshot_flag(supports_file_input),
            "audio": _normalize_snapshot_flag("audio" in input_modalities if input_modalities else None),
        },
        "outputs": {
            "text": "pass",
            "image": _normalize_snapshot_flag("image" in output_modalities if output_modalities else None),
            "audio": _normalize_snapshot_flag("audio" in output_modalities if output_modalities else None),
        },
        "operations": {
            "chat": "pass",
            "streaming": "pass",
            "tools": _normalize_snapshot_flag(
                bool(supported_parameters.intersection(OPENROUTER_TOOL_PARAMETERS))
                if supported_parameters
                else None
            ),
            "structured_output": _normalize_snapshot_flag(
                bool(supported_parameters.intersection(OPENROUTER_STRUCTURED_OUTPUT_PARAMETERS))
                if supported_parameters
                else None
            ),
            "reasoning": _normalize_snapshot_flag(
                "reasoning" in supported_parameters if supported_parameters else None
            ),
            "image_generation": _normalize_snapshot_flag(
                "image" in output_modalities if output_modalities else None
            ),
            "audio_generation": _normalize_snapshot_flag(
                "audio" in output_modalities if output_modalities else None
            ),
        },
        "limits": {
            "context_tokens": model_metadata.get("context_length"),
            "max_completion_tokens": (
                (model_metadata.get("top_provider") or {}).get("max_completion_tokens")
            ),
        },
        "model_state": {},
        "metadata": {
            "canonical_slug": model_metadata.get("canonical_slug") or "",
            "name": model_metadata.get("name") or "",
            "architecture": architecture,
        },
    }


async def fetch_openrouter_model_metadata(api_key: str, model: str, base_url: str | None) -> dict:
    """Fetch OpenRouter metadata for a specific model id."""
    if not api_key:
        raise OpenRouterMetadataAuthError("OpenRouter metadata lookup failed: missing API key.")

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        try:
            response = await client.get(get_openrouter_models_url(base_url))
        except httpx.TimeoutException as exc:
            raise OpenRouterMetadataTransientError("OpenRouter model catalog request timed out.") from exc
        except httpx.HTTPError as exc:
            raise OpenRouterMetadataTransientError(
                f"OpenRouter model catalog request failed: {exc}"
            ) from exc

    if response.status_code in {401, 403}:
        raise OpenRouterMetadataAuthError(
            "OpenRouter metadata lookup failed: invalid API key or unauthorized access."
        )
    if response.status_code >= 400:
        raise OpenRouterMetadataTransientError(
            f"OpenRouter model catalog returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenRouterMetadataTransientError("OpenRouter model catalog returned invalid JSON.") from exc

    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise OpenRouterMetadataTransientError("OpenRouter model catalog returned an unexpected payload.")

    for item in models:
        if not isinstance(item, dict):
            continue
        if item.get("id") == model or item.get("canonical_slug") == model:
            return item

    raise OpenRouterModelNotFoundError(
        f"Model `{model}` was not found in the OpenRouter catalog."
    )


async def fetch_openrouter_model_catalog(api_key: str, base_url: str | None) -> list[dict]:
    if not api_key:
        raise OpenRouterMetadataAuthError("OpenRouter metadata lookup failed: missing API key.")

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        try:
            response = await client.get(get_openrouter_models_url(base_url))
        except httpx.TimeoutException as exc:
            raise OpenRouterMetadataTransientError("OpenRouter model catalog request timed out.") from exc
        except httpx.HTTPError as exc:
            raise OpenRouterMetadataTransientError(
                f"OpenRouter model catalog request failed: {exc}"
            ) from exc

    if response.status_code in {401, 403}:
        raise OpenRouterMetadataAuthError(
            "OpenRouter metadata lookup failed: invalid API key or unauthorized access."
        )
    if response.status_code >= 400:
        raise OpenRouterMetadataTransientError(
            f"OpenRouter model catalog returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenRouterMetadataTransientError("OpenRouter model catalog returned invalid JSON.") from exc

    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise OpenRouterMetadataTransientError("OpenRouter model catalog returned an unexpected payload.")
    return [item for item in models if isinstance(item, dict)]


class OpenRouterProviderAdapter(BaseProviderAdapter):
    metadata_source_label = "OpenRouter model metadata"

    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_base_url=OPENROUTER_DEFAULT_BASE_URL,
                default_max_context_tokens=100_000,
                api_key_required=True,
                supports_model_catalog=True,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        return await complete_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key,
            base_url=get_openrouter_base_url(provider.base_url),
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
        )

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        return await stream_openai_compatible_chat(
            model=provider.model,
            api_key=provider.api_key,
            base_url=get_openrouter_base_url(provider.base_url),
            messages=messages,
            tools=tools,
            normalize_content=self.normalize_multimodal_content,
            on_content_delta=on_content_delta,
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return True

    async def list_models(self, provider) -> list[dict]:
        models = await fetch_openrouter_model_catalog(provider.api_key or "", provider.base_url)
        return [
            build_openrouter_catalog_item(item)
            for item in models
            if item.get("id")
        ]

    async def resolve_capability_snapshot(self, provider) -> dict:
        model_metadata = await fetch_openrouter_model_metadata(
            provider.api_key or "",
            provider.model,
            provider.base_url,
        )
        return build_openrouter_capability_snapshot(model_metadata)

    async def fetch_declared_capabilities(self, provider) -> dict[str, bool | None]:
        model_metadata = await fetch_openrouter_model_metadata(
            provider.api_key or "",
            provider.model,
            provider.base_url,
        )
        return parse_openrouter_declared_capabilities(model_metadata)

    async def build_native_request(self, provider, invocation_request: dict[str, object]) -> dict[str, object]:
        prompt = str(invocation_request.get("prompt") or "").strip()
        response_mode = str(invocation_request.get("response_mode") or "text").strip().lower()
        additional_config = provider.additional_config if isinstance(provider.additional_config, dict) else {}
        content = invocation_request.get("content")
        if not isinstance(content, list):
            content = []
            if prompt:
                content.append({"type": "text", "text": prompt})

            for artifact in list(invocation_request.get("artifacts") or []):
                if not isinstance(artifact, dict):
                    continue
                kind = str(artifact.get("kind") or "").strip()
                mime_type = str(
                    artifact.get("mime_type") or "application/octet-stream"
                ).strip()
                filename = str(
                    artifact.get("filename") or artifact.get("label") or "attachment"
                ).strip()
                data = str(artifact.get("data") or "").strip()
                if not data:
                    continue

                if kind == "image":
                    content.append(
                        {
                            "type": "image",
                            "source_type": "base64",
                            "data": data,
                            "mime_type": mime_type,
                            "filename": filename,
                        }
                    )
                elif kind == "pdf":
                    content.append(
                        {
                            "type": "file",
                            "source_type": "base64",
                            "data": data,
                            "mime_type": mime_type,
                            "filename": filename,
                        }
                    )
                elif kind == "audio":
                    content.append(
                        {
                            "type": "audio",
                            "source_type": "base64",
                            "data": data,
                            "mime_type": mime_type,
                            "filename": filename,
                        }
                    )

        normalized_content = self.normalize_multimodal_content(content)
        payload = {
            "model": provider.model,
            "messages": [
                {
                    "role": "user",
                    "content": normalized_content,
                }
            ],
        }
        if response_mode == "image":
            payload["modalities"] = ["text", "image"]
            image_options = additional_config.get("image_generation")
            if isinstance(image_options, dict) and image_options:
                payload.update(image_options)
        elif response_mode == "audio":
            payload["modalities"] = ["text", "audio"]
            audio_options = additional_config.get("audio")
            if isinstance(audio_options, dict) and audio_options:
                payload["audio"] = audio_options
        return payload

    def supports_native_response_mode(self, provider, response_mode: str) -> bool:
        normalized = str(response_mode or "").strip().lower()
        return normalized in {"image", "audio"}

    async def invoke_native(self, provider, invocation_request: dict[str, object]) -> dict[str, object]:
        payload = await self.build_native_request(provider, invocation_request)
        headers = {
            "Authorization": f"Bearer {provider.api_key or ''}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            response = await client.post(
                f"{get_openrouter_base_url(provider.base_url).rstrip('/')}/chat/completions",
                json=payload,
            )

        if response.status_code in {401, 403}:
            raise OpenRouterMetadataAuthError("OpenRouter request failed: invalid API key or unauthorized access.")
        if response.status_code >= 400:
            raise OpenRouterMetadataError(
                f"OpenRouter native request failed with HTTP {response.status_code}: {response.text}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise OpenRouterMetadataTransientError("OpenRouter returned invalid JSON.") from exc

    @staticmethod
    def _extract_image_payload(image_entry):
        if isinstance(image_entry, str):
            return image_entry, "", ""
        if not isinstance(image_entry, dict):
            return "", "", ""

        nested_image = image_entry.get("image_url")
        if isinstance(nested_image, dict):
            return (
                str(
                    nested_image.get("url")
                    or nested_image.get("data")
                    or nested_image.get("b64_json")
                    or ""
                ).strip(),
                str(
                    image_entry.get("mime_type")
                    or nested_image.get("mime_type")
                    or nested_image.get("media_type")
                    or ""
                ).strip(),
                str(
                    image_entry.get("filename")
                    or nested_image.get("filename")
                    or ""
                ).strip(),
            )
        if isinstance(nested_image, str):
            return (
                nested_image.strip(),
                str(image_entry.get("mime_type") or "").strip(),
                str(image_entry.get("filename") or "").strip(),
            )

        return (
            str(
                image_entry.get("data")
                or image_entry.get("b64_json")
                or image_entry.get("image_base64")
                or image_entry.get("image_data")
                or image_entry.get("url")
                or ""
            ).strip(),
            str(image_entry.get("mime_type") or image_entry.get("media_type") or "").strip(),
            str(image_entry.get("filename") or "").strip(),
        )

    async def parse_native_response(self, provider, raw_response: dict[str, object]) -> dict[str, object]:
        _provider = provider
        choices = raw_response.get("choices") or []
        message = {}
        if isinstance(choices, list) and choices:
            message = (choices[0] or {}).get("message") or {}

        content = message.get("content")
        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
                elif part.get("type") in {"image", "image_url", "output_image"}:
                    pass

        images = message.get("images") or raw_response.get("images") or []
        if not images and isinstance(content, list):
            images = [
                part
                for part in content
                if isinstance(part, dict) and part.get("type") in {"image", "image_url", "output_image"}
            ]
        if not images and isinstance(message.get("image"), (dict, str)):
            images = [message.get("image")]

        normalized_images = []
        for image in list(images or []):
            data, mime_type, filename = self._extract_image_payload(image)
            if not data:
                continue
            normalized_images.append(
                {
                    "data": data,
                    "mime_type": mime_type or "image/png",
                    "filename": filename,
                }
            )

        parsed = {
            "text": "\n".join([part for part in text_parts if part]).strip(),
            "annotations": message.get("annotations") or raw_response.get("annotations") or [],
            "images": normalized_images,
            "audio": message.get("audio") or raw_response.get("audio") or None,
            "raw_message": message,
            "raw_response": raw_response,
        }
        return parsed
