"""Generic provider validation pipeline and shared probes."""

from __future__ import annotations

import time

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from nova.models.Provider import LLMProvider, VALIDATION_CAPABILITY_ORDER
from nova.providers.base import (
    ProviderMetadataAuthError,
    ProviderMetadataTransientError,
    ProviderModelNotFoundError,
)
from nova.providers.registry import (
    create_provider_llm,
    get_provider_adapter,
    normalize_multimodal_content_for_provider,
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNSUPPORTED = "unsupported"
STATUS_NOT_RUN = "not_run"

SOURCE_PROBE = "probe"
SOURCE_METADATA = "metadata"
SOURCE_UNKNOWN = "unknown"

_VALIDATION_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


def _capability_result(
    status: str,
    message: str,
    latency_ms: int | None,
    *,
    source: str = SOURCE_PROBE,
    metadata_status: str = STATUS_NOT_RUN,
    probe_status: str | None = None,
) -> dict:
    if source == SOURCE_METADATA and metadata_status == STATUS_NOT_RUN:
        metadata_status = status
    if probe_status is None:
        probe_status = STATUS_NOT_RUN if source == SOURCE_METADATA else status

    return {
        "status": status,
        "message": message,
        "latency_ms": latency_ms,
        "source": source,
        "metadata_status": metadata_status,
        "probe_status": probe_status,
    }


def _default_capabilities() -> dict:
    return {
        capability: _capability_result(
            STATUS_NOT_RUN,
            "",
            None,
            source=SOURCE_UNKNOWN,
            metadata_status=STATUS_NOT_RUN,
            probe_status=STATUS_NOT_RUN,
        )
        for capability in VALIDATION_CAPABILITY_ORDER
    }


def _failed_capabilities(message: str) -> dict:
    return {
        capability: _capability_result(
            STATUS_FAIL,
            message,
            None,
            source=SOURCE_PROBE,
            metadata_status=STATUS_NOT_RUN,
            probe_status=STATUS_FAIL,
        )
        for capability in VALIDATION_CAPABILITY_ORDER
    }


def _format_exception_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message or exc.__class__.__name__


def _append_sentence(message: str, sentence: str) -> str:
    if not sentence:
        return message
    if not message:
        return sentence
    if sentence in message:
        return message
    return f"{message} {sentence}"


def _metadata_lookup_label(metadata_label: str) -> str:
    if metadata_label.endswith(" model metadata"):
        return metadata_label.replace(" model metadata", " metadata")
    return metadata_label


def _classify_capability_failure(capability: str, exc: Exception) -> str:
    message = _format_exception_message(exc).lower()
    unsupported_markers = {
        "streaming": ("stream", "streaming", "not implemented"),
        "tools": ("tool", "function calling", "structured output", "not implemented"),
        "vision": ("vision", "image", "multimodal", "modalit", "not implemented"),
    }
    markers = unsupported_markers.get(capability, ())
    if any(marker in message for marker in markers):
        return STATUS_UNSUPPORTED
    if "unsupported" in message or "not support" in message:
        return STATUS_UNSUPPORTED
    return STATUS_FAIL


def _collect_tool_calls(response) -> list:
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if tool_calls:
        return tool_calls

    additional_kwargs = getattr(response, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        return raw_tool_calls
    return []


def _build_capability_summary(
    capabilities: dict,
    *,
    metadata_source_label: str | None = None,
    metadata_used: bool = False,
    metadata_fallback_used: bool = False,
) -> str:
    failures = []
    for capability in VALIDATION_CAPABILITY_ORDER:
        status = (capabilities.get(capability) or {}).get("status")
        if status not in {STATUS_PASS, STATUS_NOT_RUN}:
            failures.append(f"{capability}: {status}")

    if not failures:
        summary = "Validated successfully for chat, streaming, tools, and vision."
    else:
        failure_summary = ", ".join(failures)
        summary = f"Validated with partial capabilities ({failure_summary})."

    if metadata_source_label and metadata_used:
        summary = _append_sentence(summary, f"{metadata_source_label} was used when available.")
    elif metadata_source_label and metadata_fallback_used:
        summary = _append_sentence(
            summary,
            f"{metadata_source_label} was unavailable, so active probes were used.",
        )
    return summary


def _build_invalid_result(summary: str, error_message: str) -> dict:
    return {
        "validation_status": LLMProvider.ValidationStatus.INVALID,
        "validation_summary": summary,
        "validation_capabilities": _failed_capabilities(error_message),
    }


async def _probe_chat(llm) -> dict:
    started = time.perf_counter()
    response = await llm.ainvoke([HumanMessage(content="Reply with OK.")])
    content = getattr(response, "content", None)
    latency_ms = int((time.perf_counter() - started) * 1000)
    message = "Received a chat response."
    if content:
        message = f"Received chat content: {str(content)[:80]}"
    return _capability_result(STATUS_PASS, message, latency_ms)


async def _probe_streaming(llm) -> dict:
    started = time.perf_counter()
    chunk_count = 0
    async for _chunk in llm.astream([HumanMessage(content="Reply with stream.")]):
        chunk_count += 1
        if chunk_count >= 1:
            break

    latency_ms = int((time.perf_counter() - started) * 1000)
    if chunk_count < 1:
        return _capability_result(STATUS_FAIL, "No streamed chunk was received.", latency_ms)
    return _capability_result(STATUS_PASS, "Streaming probe returned at least one chunk.", latency_ms)


def _build_validation_tool() -> StructuredTool:
    def provider_validation_echo(value: str) -> str:
        """Echo the provided value."""
        return value

    return StructuredTool.from_function(
        func=provider_validation_echo,
        name="provider_validation_echo",
        description="Echo the provided value.",
    )


async def _probe_tools(llm) -> dict:
    started = time.perf_counter()
    if not hasattr(llm, "bind_tools"):
        latency_ms = int((time.perf_counter() - started) * 1000)
        return _capability_result(
            STATUS_UNSUPPORTED,
            "This provider client has no tool binding API.",
            latency_ms,
        )

    tool_enabled_llm = llm.bind_tools([_build_validation_tool()])
    response = await tool_enabled_llm.ainvoke(
        [
            HumanMessage(
                content=(
                    "Call the provider_validation_echo tool with value `ok`."
                    " Do not answer directly."
                )
            )
        ]
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    tool_calls = _collect_tool_calls(response)
    if not tool_calls:
        return _capability_result(STATUS_FAIL, "No tool call was returned by the model.", latency_ms)
    return _capability_result(STATUS_PASS, "Tool calling probe returned a tool call.", latency_ms)


async def _probe_vision(llm) -> dict:
    started = time.perf_counter()
    payload = [
        {
            "type": "text",
            "text": "Describe the attached image in one short sentence.",
        },
        {
            "type": "image",
            "source_type": "base64",
            "data": _VALIDATION_IMAGE_BASE64,
            "mime_type": "image/png",
            "filename": "provider-validation.png",
        },
    ]
    response = await llm.ainvoke(
        [
            HumanMessage(
                content=normalize_multimodal_content_for_provider(
                    getattr(llm, "_nova_provider", None),
                    payload,
                )
            )
        ]
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    content = getattr(response, "content", None)
    message = "Vision payload accepted."
    if content:
        message = f"Vision payload accepted: {str(content)[:80]}"
    return _capability_result(STATUS_PASS, message, latency_ms)


def _build_declared_capabilities(adapter, declared_capabilities: dict[str, bool | None]) -> dict[str, dict]:
    capabilities = {}
    metadata_source_label = adapter.metadata_source_label or "Provider metadata"

    for capability, supported in declared_capabilities.items():
        if supported is None:
            continue

        capability_label = {
            "tools": "tool calling support",
            "vision": "image input support",
        }.get(capability, capability)

        capabilities[capability] = _capability_result(
            STATUS_PASS if supported else STATUS_UNSUPPORTED,
            (
                f"{metadata_source_label} declares {capability_label}."
                if supported
                else f"{metadata_source_label} does not declare {capability_label}."
            ),
            None,
            source=SOURCE_METADATA,
        )

    return capabilities


async def validate_provider_configuration(provider) -> dict:
    """Validate provider capabilities using provider-specific adapters and shared probes."""
    capabilities = _default_capabilities()
    metadata_used = False
    metadata_fallback_used = False
    adapter = get_provider_adapter(provider)

    try:
        llm = create_provider_llm(provider)
        setattr(llm, "_nova_provider", provider)
    except Exception as exc:
        error_message = _format_exception_message(exc)
        return _build_invalid_result(
            f"Validation failed during provider creation: {error_message}",
            f"Skipped after provider creation failure: {error_message}",
        )

    declared_capabilities = {}
    try:
        declared = await adapter.fetch_declared_capabilities(provider)
    except (ProviderMetadataAuthError, ProviderModelNotFoundError) as exc:
        error_message = _format_exception_message(exc)
        metadata_label = adapter.metadata_source_label or "Provider metadata"
        metadata_lookup_label = _metadata_lookup_label(metadata_label)
        return _build_invalid_result(
            f"Validation failed during {metadata_lookup_label} lookup: {error_message}",
            f"Skipped after {metadata_lookup_label} failure: {error_message}",
        )
    except ProviderMetadataTransientError:
        metadata_fallback_used = True
    else:
        declared_capabilities = _build_declared_capabilities(adapter, declared)
        metadata_used = bool(declared_capabilities)

    try:
        await llm.ainvoke([HumanMessage(content="Reply with OK.")])
    except Exception as exc:
        error_message = _format_exception_message(exc)
        return _build_invalid_result(
            f"Validation failed during connectivity/auth probe: {error_message}",
            f"Skipped after connectivity/auth failure: {error_message}",
        )

    try:
        capabilities["chat"] = await _probe_chat(llm)
    except Exception as exc:
        error_message = _format_exception_message(exc)
        capabilities["chat"] = _capability_result(
            STATUS_FAIL,
            error_message,
            None,
            source=SOURCE_PROBE,
            metadata_status=STATUS_NOT_RUN,
            probe_status=STATUS_FAIL,
        )
        for capability in ("streaming", "tools", "vision"):
            capabilities[capability] = _capability_result(
                STATUS_NOT_RUN,
                "Skipped because chat validation failed.",
                None,
                source=SOURCE_PROBE,
                metadata_status=STATUS_NOT_RUN,
                probe_status=STATUS_NOT_RUN,
            )
        return {
            "validation_status": LLMProvider.ValidationStatus.INVALID,
            "validation_summary": f"Validation failed during chat probe: {error_message}",
            "validation_capabilities": capabilities,
        }

    for capability, probe in (
        ("streaming", _probe_streaming),
        ("tools", _probe_tools),
        ("vision", _probe_vision),
    ):
        if capability in declared_capabilities:
            capabilities[capability] = declared_capabilities[capability]
            continue

        try:
            capabilities[capability] = await probe(llm)
        except Exception as exc:
            status = _classify_capability_failure(capability, exc)
            capabilities[capability] = _capability_result(
                status,
                _format_exception_message(exc),
                None,
                source=SOURCE_PROBE,
                metadata_status=STATUS_NOT_RUN,
                probe_status=status,
            )

    return {
        "validation_status": LLMProvider.ValidationStatus.VALID,
        "validation_summary": _build_capability_summary(
            capabilities,
            metadata_source_label=adapter.metadata_source_label if metadata_used or metadata_fallback_used else None,
            metadata_used=metadata_used,
            metadata_fallback_used=metadata_fallback_used,
        ),
        "validation_capabilities": capabilities,
    }
