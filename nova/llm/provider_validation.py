"""Active validation probes for LLM providers."""

from __future__ import annotations

import time

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import create_provider_llm, normalize_multimodal_content_for_provider
from nova.models.Provider import LLMProvider, VALIDATION_CAPABILITY_ORDER

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNSUPPORTED = "unsupported"
STATUS_NOT_RUN = "not_run"

_VALIDATION_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


def _capability_result(status: str, message: str, latency_ms: int | None) -> dict:
    return {
        "status": status,
        "message": message,
        "latency_ms": latency_ms,
    }


def _default_capabilities() -> dict:
    return {
        capability: _capability_result(STATUS_NOT_RUN, "", None)
        for capability in VALIDATION_CAPABILITY_ORDER
    }


def _format_exception_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message or exc.__class__.__name__


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
        return "Validated successfully for chat, streaming, tools, and vision."

    failure_summary = ", ".join(failures)
    return f"Validated with partial capabilities ({failure_summary})."


async def _probe_chat(llm) -> dict:
    started = time.perf_counter()
    response = await llm.ainvoke(
        [HumanMessage(content="Reply with OK.")],
    )
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
        return _capability_result(STATUS_UNSUPPORTED, "This provider client has no tool binding API.", latency_ms)

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


async def validate_provider_configuration(provider: LLMProvider) -> dict:
    capabilities = _default_capabilities()

    try:
        llm = create_provider_llm(provider)
        setattr(llm, "_nova_provider", provider)
    except Exception as exc:
        error_message = _format_exception_message(exc)
        for capability in VALIDATION_CAPABILITY_ORDER:
            capabilities[capability] = _capability_result(
                STATUS_FAIL,
                f"Skipped after connectivity/auth failure: {error_message}",
                None,
            )
        return {
            "validation_status": LLMProvider.ValidationStatus.INVALID,
            "validation_summary": f"Validation failed during provider creation: {error_message}",
            "validation_capabilities": capabilities,
        }

    try:
        await llm.ainvoke([HumanMessage(content="Reply with OK.")])
    except Exception as exc:
        error_message = _format_exception_message(exc)
        for capability in VALIDATION_CAPABILITY_ORDER:
            capabilities[capability] = _capability_result(
                STATUS_FAIL,
                f"Skipped after connectivity/auth failure: {error_message}",
                None,
            )
        return {
            "validation_status": LLMProvider.ValidationStatus.INVALID,
            "validation_summary": f"Validation failed during connectivity/auth probe: {error_message}",
            "validation_capabilities": capabilities,
        }

    try:
        capabilities["chat"] = await _probe_chat(llm)
    except Exception as exc:
        capabilities["chat"] = _capability_result(
            STATUS_FAIL,
            _format_exception_message(exc),
            None,
        )
        for capability in ("streaming", "tools", "vision"):
            capabilities[capability] = _capability_result(
                STATUS_NOT_RUN,
                "Skipped because chat validation failed.",
                None,
            )
        return {
            "validation_status": LLMProvider.ValidationStatus.INVALID,
            "validation_summary": (
                "Validation failed during chat probe: "
                f"{_format_exception_message(exc)}"
            ),
            "validation_capabilities": capabilities,
        }

    for capability, probe in (
        ("streaming", _probe_streaming),
        ("tools", _probe_tools),
        ("vision", _probe_vision),
    ):
        try:
            capabilities[capability] = await probe(llm)
        except Exception as exc:
            capabilities[capability] = _capability_result(
                _classify_capability_failure(capability, exc),
                _format_exception_message(exc),
                None,
            )

    return {
        "validation_status": LLMProvider.ValidationStatus.VALID,
        "validation_summary": _build_capability_summary(capabilities),
        "validation_capabilities": capabilities,
    }
