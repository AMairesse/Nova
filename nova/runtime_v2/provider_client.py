from __future__ import annotations

from typing import Awaitable, Callable

from openai import AsyncOpenAI


class OpenAICompatibleProviderClient:
    def __init__(self, provider):
        if provider is None:
            raise ValueError("React Terminal V1 requires an LLM provider.")
        model = str(getattr(provider, "model", "") or "").strip()
        if not model:
            raise ValueError("The selected provider has no model configured.")

        base_url = str(getattr(provider, "base_url", "") or "").strip() or None
        api_key = getattr(provider, "api_key", None) or "nova-runtime-v2"
        self.provider = provider
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    @property
    def max_context_tokens(self) -> int | None:
        value = getattr(self.provider, "max_context_tokens", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _build_completion_kwargs(self, *, messages: list[dict], tools: list[dict] | None, stream: bool) -> dict:
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        additional_config = getattr(self.provider, "additional_config", None) or {}
        temperature = additional_config.get("temperature")
        if isinstance(temperature, (int, float)):
            kwargs["temperature"] = float(temperature)
        return kwargs

    @staticmethod
    def _extract_total_tokens(usage) -> int | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
        else:
            value = getattr(usage, "total_tokens", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def create_chat_completion(self, *, messages: list[dict], tools: list[dict] | None = None):
        kwargs = self._build_completion_kwargs(messages=messages, tools=tools, stream=False)

        completion = await self.client.chat.completions.create(**kwargs)
        choice = completion.choices[0].message
        tool_calls = []
        for tool_call in list(choice.tool_calls or []):
            function = getattr(tool_call, "function", None)
            tool_calls.append(
                {
                    "id": getattr(tool_call, "id", ""),
                    "name": getattr(function, "name", ""),
                    "arguments": getattr(function, "arguments", "") or "{}",
                }
            )

        return {
            "content": choice.content or "",
            "tool_calls": tool_calls,
            "usage": getattr(completion, "usage", None),
            "total_tokens": self._extract_total_tokens(getattr(completion, "usage", None)),
            "streamed": False,
        }

    async def stream_chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ):
        kwargs = self._build_completion_kwargs(messages=messages, tools=tools, stream=True)
        stream = await self.client.chat.completions.create(**kwargs)
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict] = {}
        usage = None

        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            for choice in list(getattr(chunk, "choices", None) or []):
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    text = str(content)
                    content_parts.append(text)
                    if on_content_delta:
                        await on_content_delta(text)
                for tool_call in list(getattr(delta, "tool_calls", None) or []):
                    try:
                        index = int(getattr(tool_call, "index", 0))
                    except (TypeError, ValueError):
                        index = len(tool_calls_by_index)
                    record = tool_calls_by_index.setdefault(
                        index,
                        {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        },
                    )
                    tool_id = getattr(tool_call, "id", None)
                    if tool_id:
                        record["id"] = str(tool_id)
                    function = getattr(tool_call, "function", None)
                    function_name = getattr(function, "name", None)
                    if function_name:
                        record["name"] = str(function_name)
                    function_arguments = getattr(function, "arguments", None)
                    if function_arguments:
                        record["arguments"] += str(function_arguments)

        return {
            "content": "".join(content_parts),
            "tool_calls": [tool_calls_by_index[idx] for idx in sorted(tool_calls_by_index.keys())],
            "usage": usage,
            "total_tokens": self._extract_total_tokens(usage),
            "streamed": True,
        }
