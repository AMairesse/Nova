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
    create_openai_compatible_llm,
    normalize_openai_compatible_multimodal_content,
)

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_ALLOWED_PATHS = {"", "/", "/api", "/api/", "/api/v1", "/api/v1/"}
OPENROUTER_TOOL_PARAMETERS = {"tools", "tool_choice", "parallel_tool_calls"}


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


class OpenRouterProviderAdapter(BaseProviderAdapter):
    metadata_source_label = "OpenRouter model metadata"

    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_base_url=OPENROUTER_DEFAULT_BASE_URL,
                default_max_context_tokens=100_000,
                api_key_required=True,
            )
        )

    def create_llm(self, provider):
        return create_openai_compatible_llm(
            model=provider.model,
            api_key=provider.api_key,
            base_url=get_openrouter_base_url(provider.base_url),
        )

    def normalize_multimodal_content(self, content):
        return normalize_openai_compatible_multimodal_content(content)

    async def fetch_declared_capabilities(self, provider) -> dict[str, bool | None]:
        model_metadata = await fetch_openrouter_model_metadata(
            provider.api_key or "",
            provider.model,
            provider.base_url,
        )
        return parse_openrouter_declared_capabilities(model_metadata)
