"""Shared helpers for OpenAI-compatible provider adapters."""

from __future__ import annotations

import json
import mimetypes
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI


def create_openai_compatible_client(*, api_key: str | None, base_url: str | None) -> AsyncOpenAI:
    """Build an AsyncOpenAI client with Nova's common defaults."""
    return AsyncOpenAI(
        api_key=str(api_key or "nova"),
        base_url=base_url,
        max_retries=2,
    )


def _normalize_usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump(mode="json", exclude_none=True)
    return dict(usage)


def _extract_total_tokens(usage: dict[str, Any] | None) -> int | None:
    usage = usage or {}
    total = usage.get("total_tokens")
    if total is None:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if prompt_tokens is not None and completion_tokens is not None:
            total = int(prompt_tokens) + int(completion_tokens)
    try:
        return int(total) if total is not None else None
    except (TypeError, ValueError):
        return None


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "text":
            text_parts.append(str(item.get("text") or ""))
        elif item_type == "output_text":
            text_parts.append(str(item.get("text") or ""))
        elif item_type == "refusal":
            text_parts.append(str(item.get("refusal") or ""))
    return "".join(text_parts)


def _coerce_model_dump(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json", exclude_none=True)
    return dict(payload)


def _normalize_tool_calls(raw_tool_calls: list[Any] | None) -> list[dict[str, str]]:
    tool_calls: list[dict[str, str]] = []
    for index, item in enumerate(list(raw_tool_calls or []), start=1):
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json", exclude_none=True)
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = (
            str(item.get("name") or "").strip()
            or str(function_payload.get("name") or "").strip()
        )
        arguments = function_payload.get("arguments")
        if arguments is None:
            arguments = item.get("arguments")
        if isinstance(arguments, str):
            serialized_arguments = arguments
        elif isinstance(arguments, dict):
            serialized_arguments = json.dumps(arguments, ensure_ascii=False)
        else:
            serialized_arguments = "{}"
        tool_calls.append(
            {
                "id": str(item.get("id") or f"call_{index}"),
                "name": name,
                "arguments": serialized_arguments or "{}",
            }
        )
    return tool_calls


def _merge_stream_tool_calls(
    tool_states: dict[int, dict[str, Any]],
    raw_tool_calls: list[Any] | None,
) -> None:
    for position, item in enumerate(list(raw_tool_calls or [])):
        item = _coerce_model_dump(item)
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function")
        if hasattr(function_payload, "model_dump"):
            function_payload = function_payload.model_dump(mode="json", exclude_none=True)
        if not isinstance(function_payload, dict):
            function_payload = {}

        raw_index = item.get("index")
        try:
            tool_index = int(raw_index) if raw_index is not None else position
        except (TypeError, ValueError):
            tool_index = position

        state = tool_states.setdefault(
            tool_index,
            {
                "id": "",
                "name": "",
                "arguments_parts": [],
            },
        )
        tool_id = str(item.get("id") or "").strip()
        if tool_id:
            state["id"] = tool_id

        name = str(function_payload.get("name") or item.get("name") or "").strip()
        if name:
            state["name"] = name

        arguments = function_payload.get("arguments")
        if isinstance(arguments, str):
            state["arguments_parts"].append(arguments)
        elif isinstance(arguments, dict):
            state["arguments_parts"].append(json.dumps(arguments, ensure_ascii=False))


def _finalize_stream_tool_calls(tool_states: dict[int, dict[str, Any]]) -> list[dict[str, str]]:
    tool_calls: list[dict[str, str]] = []
    for index, state in sorted(tool_states.items()):
        tool_calls.append(
            {
                "id": str(state.get("id") or f"call_{index + 1}"),
                "name": str(state.get("name") or "").strip(),
                "arguments": "".join(state.get("arguments_parts") or []) or "{}",
            }
        )
    return tool_calls


async def collect_openai_like_stream(
    stream,
    *,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_states: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    last_payload: dict[str, Any] | None = None

    async for chunk in stream:
        payload = _coerce_model_dump(chunk)
        last_payload = payload
        usage = _normalize_usage(payload.get("usage")) or usage
        for choice in list(payload.get("choices") or []):
            if hasattr(choice, "model_dump"):
                choice = choice.model_dump(mode="json", exclude_none=True)
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if hasattr(delta, "model_dump"):
                delta = delta.model_dump(mode="json", exclude_none=True)
            if not isinstance(delta, dict):
                continue

            content_delta = _extract_text_content(delta.get("content"))
            if content_delta:
                content_parts.append(content_delta)
                if on_content_delta:
                    await on_content_delta(content_delta)

            _merge_stream_tool_calls(tool_states, delta.get("tool_calls"))

    return {
        "content": "".join(content_parts),
        "tool_calls": _finalize_stream_tool_calls(tool_states),
        "usage": usage,
        "total_tokens": _extract_total_tokens(usage),
        "streamed": True,
        "streaming_mode": "native",
        "raw_response": last_payload or {},
    }


def build_openai_compatible_messages(
    messages: list[dict[str, Any]],
    *,
    normalize_content,
) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    for message in list(messages or []):
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            continue

        payload: dict[str, Any] = {"role": role}
        content = message.get("content", "")

        if role in {"system", "user"}:
            payload["content"] = normalize_content(content) if isinstance(content, list) else str(content or "")
        elif role == "assistant":
            payload["content"] = (
                normalize_content(content)
                if isinstance(content, list)
                else (None if content is None or content == "" else str(content))
            )
            tool_calls = list(message.get("tool_calls") or [])
            if tool_calls:
                payload["tool_calls"] = tool_calls
        elif role == "tool":
            payload["content"] = str(content or "")
            payload["tool_call_id"] = str(message.get("tool_call_id") or "")

        normalized_messages.append(payload)
    return normalized_messages


def normalize_openai_completion_payload(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    message = {}
    if isinstance(choices, list) and choices:
        message = (choices[0] or {}).get("message") or {}

    usage = _normalize_usage(payload.get("usage"))
    return {
        "content": _extract_text_content(message.get("content")),
        "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
        "usage": usage,
        "total_tokens": _extract_total_tokens(usage),
        "streamed": False,
        "streaming_mode": "none",
        "raw_response": payload,
    }


async def complete_openai_compatible_chat(
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    normalize_content,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = create_openai_compatible_client(api_key=api_key, base_url=base_url)
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": build_openai_compatible_messages(
            messages,
            normalize_content=normalize_content,
        ),
        "temperature": 0,
    }
    if tools:
        request_payload["tools"] = tools
    if extra_kwargs:
        request_payload.update(extra_kwargs)
    response = await client.chat.completions.create(**request_payload)
    payload = response.model_dump(mode="json", exclude_none=True)
    return normalize_openai_completion_payload(payload)


async def stream_openai_compatible_chat(
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    normalize_content,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = create_openai_compatible_client(api_key=api_key, base_url=base_url)
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": build_openai_compatible_messages(
            messages,
            normalize_content=normalize_content,
        ),
        "temperature": 0,
        "stream": True,
    }
    if tools:
        request_payload["tools"] = tools
    if extra_kwargs:
        request_payload.update(extra_kwargs)
    stream = await client.chat.completions.create(**request_payload)
    return await collect_openai_like_stream(
        stream,
        on_content_delta=on_content_delta,
    )


def normalize_openai_compatible_multimodal_content(content):
    """Translate Nova's internal multimodal blocks to the OpenAI-compatible wire format."""
    if not isinstance(content, list):
        return content

    normalized = []
    for part in content:
        if not isinstance(part, dict):
            normalized.append(part)
            continue

        part_type = part.get("type")
        source_type = part.get("source_type")

        if part_type == "image" and source_type == "base64":
            mime_type = part.get("mime_type") or "application/octet-stream"
            data = part.get("data") or ""
            normalized.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{data}",
                    },
                }
            )
            continue

        if part_type == "file" and source_type == "base64":
            mime_type = part.get("mime_type") or "application/octet-stream"
            data = part.get("data") or ""
            filename = part.get("filename") or mimetypes.guess_extension(mime_type) or "attachment"
            normalized.append(
                {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:{mime_type};base64,{data}",
                    },
                }
            )
            continue

        if part_type == "audio" and source_type == "base64":
            mime_type = str(part.get("mime_type") or "").lower()
            data = part.get("data") or ""
            audio_format = "wav"
            if "mpeg" in mime_type or mime_type.endswith("/mp3"):
                audio_format = "mp3"
            elif mime_type.endswith("/ogg"):
                audio_format = "ogg"
            normalized.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": data,
                        "format": audio_format,
                    },
                }
            )
            continue

        if part_type != "image" or source_type != "base64":
            normalized.append(part)
            continue
    return normalized
