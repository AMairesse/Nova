"""Base classes and shared types for provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ProviderDefaults:
    """Serializable provider defaults used by runtime and forms."""

    default_base_url: str = ""
    default_max_context_tokens: int = 4096
    api_key_required: bool = True
    supports_model_catalog: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderMetadataError(Exception):
    """Base error for provider metadata lookups."""


class ProviderMetadataAuthError(ProviderMetadataError):
    """Authentication or authorization failed."""


class ProviderModelNotFoundError(ProviderMetadataError):
    """The configured model was not found in the provider catalog."""


class ProviderMetadataTransientError(ProviderMetadataError):
    """Provider metadata lookup failed transiently."""


class BaseProviderAdapter:
    """Contract for provider-specific runtime and validation behavior."""

    metadata_source_label = "Provider metadata"

    def __init__(self, defaults: ProviderDefaults) -> None:
        self._defaults = defaults

    def get_defaults(self) -> ProviderDefaults:
        return self._defaults

    def normalize_multimodal_content(self, content):
        return content

    async def complete_chat(
        self,
        provider,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def stream_chat(
        self,
        provider,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        completion = await self.complete_chat(
            provider,
            messages=messages,
            tools=tools,
        )
        content = str(completion.get("content") or "")
        if content and on_content_delta:
            await on_content_delta(content)
        completion["streamed"] = True
        return completion

    async def prepare_turn_content(self, provider, intro_text, resolved_inputs, **kwargs):
        from nova.turn_inputs import prepare_turn_content

        return await prepare_turn_content(
            provider,
            intro_text,
            resolved_inputs,
            **kwargs,
        )

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return False

    def build_validation_pdf_content(self, provider, *, pdf_base64: str):
        return [
            {
                "type": "text",
                "text": (
                    "Confirm that you can access the attached PDF. "
                    "Reply in one short sentence."
                ),
            },
            {
                "type": "file",
                "source_type": "base64",
                "data": pdf_base64,
                "mime_type": "application/pdf",
                "filename": "provider-validation.pdf",
            },
        ]

    async def list_models(self, provider) -> list[dict[str, Any]]:
        return []

    async def resolve_capability_snapshot(self, provider) -> dict[str, Any]:
        return {}

    async def fetch_declared_capabilities(self, provider) -> dict[str, bool | None]:
        return {}

    async def build_native_request(self, provider, invocation_request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def invoke_native(self, provider, invocation_request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def parse_native_response(self, provider, raw_response: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
