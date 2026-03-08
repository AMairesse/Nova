"""Generic provider validation pipeline and shared probes."""

from __future__ import annotations

import time

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from nova.models.Provider import LLMProvider, VALIDATION_CAPABILITY_ORDER
from nova.providers.registry import (
    create_provider_llm,
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
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAD/wAARCAAgACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3ePn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3ePn6/9sAQwACAgICAgIDAgIDBQMDAwUGBQUFBQYIBgYGBgYICggICAgICAoKCgoKCgoKDAwMDAwMDg4ODg4PDw8PDw8PDw8P/9sAQwECAgIEBAQHBAQHEAsJCxAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ/90ABAAC/9oADAMBAAIRAxEAPwD9/KKKKACiiigD/9D9/KKKKACiiigD/9k="
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


def _build_capability_summary(capabilities: dict) -> str:
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
    return summary


def _build_invalid_result(summary: str, error_message: str) -> dict:
    return {
        "validation_status": LLMProvider.ValidationStatus.INVALID,
        "verification_summary": summary,
        "verified_operations": _failed_capabilities(error_message),
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
            "mime_type": "image/jpeg",
            "filename": "provider-validation.jpg",
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


async def validate_provider_configuration(provider) -> dict:
    """Validate provider capabilities using provider-specific adapters and shared probes."""
    if not str(getattr(provider, "model", "") or "").strip():
        return _build_invalid_result(
            "Validation requires a selected model.",
            "Skipped because no model is configured.",
        )

    capabilities = _default_capabilities()

    try:
        llm = create_provider_llm(provider)
        setattr(llm, "_nova_provider", provider)
    except Exception as exc:
        error_message = _format_exception_message(exc)
        return _build_invalid_result(
            f"Validation failed during provider creation: {error_message}",
            f"Skipped after provider creation failure: {error_message}",
        )

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
            "verification_summary": f"Validation failed during chat probe: {error_message}",
            "verified_operations": capabilities,
        }

    for capability, probe in (
        ("streaming", _probe_streaming),
        ("tools", _probe_tools),
        ("vision", _probe_vision),
    ):
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
        "verification_summary": _build_capability_summary(capabilities),
        "verified_operations": capabilities,
    }
