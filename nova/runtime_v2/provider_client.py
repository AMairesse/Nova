from __future__ import annotations

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

    async def create_chat_completion(self, *, messages: list[dict], tools: list[dict]):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }

        additional_config = getattr(self.provider, "additional_config", None) or {}
        temperature = additional_config.get("temperature")
        if isinstance(temperature, (int, float)):
            kwargs["temperature"] = float(temperature)

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
        }
