"""Ollama provider adapter."""

from __future__ import annotations

import json
from typing import Any

import ollama

from nova.providers.base import BaseProviderAdapter, ProviderDefaults

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"


def _normalize_ollama_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    prompt_tokens = payload.get("prompt_eval_count")
    completion_tokens = payload.get("eval_count")
    if prompt_tokens is None and completion_tokens is None:
        return None
    total_tokens = None
    if prompt_tokens is not None and completion_tokens is not None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _normalize_ollama_tool_calls(raw_tool_calls: list[Any] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(list(raw_tool_calls or []), start=1):
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json", exclude_none=True)
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function") if isinstance(item.get("function"), dict) else {}
        arguments = function_payload.get("arguments")
        if isinstance(arguments, str):
            serialized_arguments = arguments
        elif isinstance(arguments, dict):
            serialized_arguments = json.dumps(arguments, ensure_ascii=False)
        else:
            serialized_arguments = "{}"
        normalized.append(
            {
                "id": str(item.get("id") or f"call_{index}"),
                "name": str(function_payload.get("name") or item.get("name") or "").strip(),
                "arguments": serialized_arguments or "{}",
            }
        )
    return normalized


def _normalize_ollama_message(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "").strip().lower()
    payload: dict[str, Any] = {"role": role}
    content = message.get("content", "")

    if isinstance(content, list):
        text_parts: list[str] = []
        images: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip().lower()
            source_type = str(part.get("source_type") or "").strip().lower()
            if part_type == "text":
                text_parts.append(str(part.get("text") or ""))
            elif part_type == "image" and source_type == "base64":
                data = str(part.get("data") or "").strip()
                if data:
                    images.append(data)
        payload["content"] = "".join(text_parts).strip()
        if images:
            payload["images"] = images
    else:
        payload["content"] = str(content or "")

    if role == "assistant":
        tool_calls = list(message.get("tool_calls") or [])
        if tool_calls:
            payload["tool_calls"] = [
                {
                    "function": {
                        "name": str((item.get("function") or {}).get("name") or ""),
                        "arguments": json.loads(str((item.get("function") or {}).get("arguments") or "{}")),
                    }
                }
                for item in tool_calls
                if isinstance(item, dict)
            ]
    elif role == "tool":
        payload["tool_name"] = str(message.get("tool_name") or "")

    return payload


def _normalize_ollama_response(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") or {}
    usage = _normalize_ollama_usage(payload)
    return {
        "content": str(message.get("content") or ""),
        "tool_calls": _normalize_ollama_tool_calls(message.get("tool_calls")),
        "usage": usage,
        "total_tokens": (usage or {}).get("total_tokens"),
        "streamed": False,
        "raw_response": payload,
    }


class OllamaProviderAdapter(BaseProviderAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProviderDefaults(
                default_base_url=OLLAMA_DEFAULT_BASE_URL,
                default_max_context_tokens=4_096,
                api_key_required=False,
            )
        )

    async def complete_chat(self, provider, *, messages, tools=None):
        client = ollama.AsyncClient(host=provider.base_url or OLLAMA_DEFAULT_BASE_URL)
        response = await client.chat(
            model=provider.model,
            messages=[_normalize_ollama_message(message) for message in list(messages or [])],
            tools=tools or None,
            stream=False,
            think=False,
        )
        return _normalize_ollama_response(response.model_dump(mode="json", exclude_none=True))

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        if tools:
            return await super().stream_chat(
                provider,
                messages=messages,
                tools=tools,
                on_content_delta=on_content_delta,
            )

        client = ollama.AsyncClient(host=provider.base_url or OLLAMA_DEFAULT_BASE_URL)
        stream = await client.chat(
            model=provider.model,
            messages=[_normalize_ollama_message(message) for message in list(messages or [])],
            stream=True,
            think=False,
        )
        content_parts: list[str] = []
        last_payload: dict[str, Any] | None = None
        async for chunk in stream:
            payload = chunk.model_dump(mode="json", exclude_none=True)
            last_payload = payload
            delta = str((payload.get("message") or {}).get("content") or "")
            if delta:
                content_parts.append(delta)
                if on_content_delta:
                    await on_content_delta(delta)

        normalized = _normalize_ollama_response(last_payload or {"message": {}})
        normalized["content"] = "".join(content_parts) or normalized.get("content") or ""
        normalized["streamed"] = True
        return normalized
