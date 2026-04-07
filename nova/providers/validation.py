"""Generic provider validation pipeline and shared probes."""

from __future__ import annotations

import time

from nova.models.Provider import LLMProvider, VALIDATION_CAPABILITY_ORDER
from nova.providers.registry import (
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
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAD/wAARCAAgACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3ePn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3ePn6/9sAQwACAgICAgIDAgIDBQMDAwUGBQUFBQYIBgYGBgYICggICAgICAoKCgoKCgoKDAwMDAwMDg4ODg4PDw8PDw8PDw8P/9sAQwECAgIEBAQHBAQHEAsJCxAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ/90ABAAC/9oADAMBAAIRAxEAPwD9/KKKKACiiigD/9D9/KKKKACiiigD/9k="
)
_VALIDATION_PDF_BASE64 = (
    "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMjAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjI1NgolJUVPRgo="
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
        "tools": (
            "tool",
            "tool use",
            "tool calling",
            "function calling",
            "no endpoints found that support tool use",
            "structured output",
            "not implemented",
        ),
        "vision": ("vision", "image", "multimodal", "modalit", "not implemented"),
        "pdf": (
            "pdf",
            "document",
            "file input",
            "file upload",
            "not implemented",
        ),
    }
    markers = unsupported_markers.get(capability, ())
    if any(marker in message for marker in markers):
        return STATUS_UNSUPPORTED
    if "unsupported" in message or "not support" in message:
        return STATUS_UNSUPPORTED
    return STATUS_FAIL


def _build_capability_summary(capabilities: dict) -> str:
    return _build_validation_summary(capabilities, {})


def _build_validation_summary(
    verified_operations: dict,
    verified_inputs: dict,
) -> str:
    failures = []
    for capability in VALIDATION_CAPABILITY_ORDER:
        status = (verified_operations.get(capability) or {}).get("status")
        if status not in {STATUS_PASS, STATUS_NOT_RUN}:
            failures.append(f"{capability}: {status}")

    pdf_status = (verified_inputs.get("pdf") or {}).get("status")
    if pdf_status not in {None, STATUS_PASS, STATUS_NOT_RUN}:
        failures.append(f"pdf input: {pdf_status}")

    if not failures:
        if pdf_status == STATUS_PASS:
            summary = "Validated successfully for chat, streaming, tools, vision, and PDF input."
        else:
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


async def _probe_chat(adapter, provider) -> dict:
    started = time.perf_counter()
    response = await adapter.complete_chat(
        provider,
        messages=[{"role": "user", "content": "Reply with OK."}],
        tools=None,
    )
    content = response.get("content")
    latency_ms = int((time.perf_counter() - started) * 1000)
    message = "Received a chat response."
    if content:
        message = f"Received chat content: {str(content)[:80]}"
    return _capability_result(STATUS_PASS, message, latency_ms)


async def _probe_streaming(adapter, provider) -> dict:
    started = time.perf_counter()
    chunk_count = 0

    async def _count_delta(_delta: str) -> None:
        nonlocal chunk_count
        chunk_count += 1

    await adapter.stream_chat(
        provider,
        messages=[{"role": "user", "content": "Reply with stream."}],
        tools=None,
        on_content_delta=_count_delta,
    )

    latency_ms = int((time.perf_counter() - started) * 1000)
    if chunk_count < 1:
        return _capability_result(STATUS_FAIL, "No streamed chunk was received.", latency_ms)
    return _capability_result(STATUS_PASS, "Streaming probe returned at least one chunk.", latency_ms)


def _build_validation_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "provider_validation_echo",
            "description": "Echo the provided value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                    }
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }


async def _probe_tools(adapter, provider) -> dict:
    started = time.perf_counter()
    response = await adapter.complete_chat(
        provider,
        messages=[
            {
                "role": "user",
                "content": "Call the provider_validation_echo tool with value `ok`. Do not answer directly.",
            }
        ],
        tools=[_build_validation_tool()],
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    tool_calls = list(response.get("tool_calls") or [])
    if not tool_calls:
        return _capability_result(STATUS_FAIL, "No tool call was returned by the model.", latency_ms)
    return _capability_result(STATUS_PASS, "Tool calling probe returned a tool call.", latency_ms)


async def _probe_vision(adapter, provider) -> dict:
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
    response = await adapter.complete_chat(
        provider,
        messages=[
            {
                "role": "user",
                "content": normalize_multimodal_content_for_provider(provider, payload),
            }
        ],
        tools=None,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    content = response.get("content")
    message = "Vision payload accepted."
    if content:
        message = f"Vision payload accepted: {str(content)[:80]}"
    return _capability_result(STATUS_PASS, message, latency_ms)


def _default_verified_inputs() -> dict:
    return {
        "pdf": _capability_result(
            STATUS_NOT_RUN,
            "",
            None,
            source=SOURCE_UNKNOWN,
            metadata_status=STATUS_NOT_RUN,
            probe_status=STATUS_NOT_RUN,
        )
    }


async def _probe_pdf(provider, adapter) -> dict:
    started = time.perf_counter()
    if not adapter.supports_active_pdf_input_probe(provider):
        latency_ms = int((time.perf_counter() - started) * 1000)
        return _capability_result(
            STATUS_NOT_RUN,
            "Active PDF probe is not enabled for this provider type.",
            latency_ms,
            source=SOURCE_PROBE,
            metadata_status=STATUS_NOT_RUN,
            probe_status=STATUS_NOT_RUN,
        )

    payload = adapter.build_validation_pdf_content(
        provider,
        pdf_base64=_VALIDATION_PDF_BASE64,
    )
    response = await adapter.complete_chat(
        provider,
        messages=[
            {
                "role": "user",
                "content": normalize_multimodal_content_for_provider(provider, payload),
            }
        ],
        tools=None,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    content = response.get("content")
    message = "PDF input payload accepted."
    if content:
        message = f"PDF input payload accepted: {str(content)[:80]}"
    return _capability_result(STATUS_PASS, message, latency_ms)


async def validate_provider_configuration(provider) -> dict:
    """Validate provider capabilities using provider-specific adapters and shared probes."""
    if not str(getattr(provider, "model", "") or "").strip():
        return _build_invalid_result(
            "Validation requires a selected model.",
            "Skipped because no model is configured.",
        )

    capabilities = _default_capabilities()
    verified_inputs = _default_verified_inputs()

    try:
        adapter = get_provider_adapter(provider)
    except Exception as exc:
        error_message = _format_exception_message(exc)
        return _build_invalid_result(
            f"Validation failed during provider creation: {error_message}",
            f"Skipped after provider creation failure: {error_message}",
        )

    try:
        await adapter.complete_chat(
            provider,
            messages=[{"role": "user", "content": "Reply with OK."}],
            tools=None,
        )
    except Exception as exc:
        error_message = _format_exception_message(exc)
        return _build_invalid_result(
            f"Validation failed during connectivity/auth probe: {error_message}",
            f"Skipped after connectivity/auth failure: {error_message}",
        )

    try:
        capabilities["chat"] = await _probe_chat(adapter, provider)
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
            "verified_inputs": verified_inputs,
        }

    for capability, probe in (
        ("streaming", _probe_streaming),
        ("tools", _probe_tools),
        ("vision", _probe_vision),
    ):
        try:
            capabilities[capability] = await probe(adapter, provider)
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

    try:
        verified_inputs["pdf"] = await _probe_pdf(provider, adapter)
    except Exception as exc:
        status = _classify_capability_failure("pdf", exc)
        verified_inputs["pdf"] = _capability_result(
            status,
            _format_exception_message(exc),
            None,
            source=SOURCE_PROBE,
            metadata_status=STATUS_NOT_RUN,
            probe_status=status,
        )

    return {
        "validation_status": LLMProvider.ValidationStatus.VALID,
        "verification_summary": _build_validation_summary(
            capabilities,
            verified_inputs,
        ),
        "verified_operations": capabilities,
        "verified_inputs": verified_inputs,
    }
