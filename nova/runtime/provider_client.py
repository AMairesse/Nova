from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from nova.providers.registry import create_provider_llm


def _terminal_tool(command: str) -> str:
    """Execute one shell-like command in the Nova runtime."""
    return command


def _delegate_to_agent_tool(
    agent_id: str,
    question: str,
    input_paths: list[str] | None = None,
) -> str:
    """Delegate a focused task to one configured sub-agent."""
    return question


def _ask_user_tool(question: str, schema: dict | None = None) -> str:
    """Ask the end-user a single blocking clarification question."""
    return question


class ProviderClient:
    def __init__(self, provider):
        if provider is None:
            raise ValueError("React Terminal requires an LLM provider.")
        model = str(getattr(provider, "model", "") or "").strip()
        if not model:
            raise ValueError("The selected provider has no model configured.")

        self.provider = provider
        self.model = model
        self.client = create_provider_llm(provider)

    @property
    def max_context_tokens(self) -> int | None:
        value = getattr(self.provider, "max_context_tokens", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_total_tokens(response: Any) -> int | None:
        usage = getattr(response, "usage_metadata", None) or {}
        total = usage.get("total_tokens")
        if total is None:
            total = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if total is not None and output_tokens is not None:
                total = int(total) + int(output_tokens)
        try:
            return int(total) if total is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _assistant_tool_calls_from_message(message: Any) -> list[dict[str, str]]:
        raw_tool_calls = list(getattr(message, "tool_calls", []) or [])
        if not raw_tool_calls:
            additional_kwargs = getattr(message, "additional_kwargs", None) or {}
            raw_tool_calls = list(additional_kwargs.get("tool_calls") or [])

        tool_calls: list[dict[str, str]] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            function_payload = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = (
                str(item.get("name") or "").strip()
                or str(function_payload.get("name") or "").strip()
            )
            arguments_payload = item.get("args")
            if arguments_payload is None:
                arguments_payload = function_payload.get("arguments")
            if arguments_payload is None:
                arguments_payload = {}
            if isinstance(arguments_payload, str):
                arguments = arguments_payload
            else:
                import json

                arguments = json.dumps(arguments_payload, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": name,
                    "arguments": arguments or "{}",
                }
            )
        return tool_calls

    @staticmethod
    def _serialize_ai_content(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
                else:
                    text_parts.append(str(item))
            return "".join(text_parts)
        return str(content or "")

    @staticmethod
    def _build_langchain_messages(messages: list[dict[str, Any]]) -> list[Any]:
        built: list[Any] = []
        for message in list(messages or []):
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content", "")
            if role == "system":
                built.append(SystemMessage(content=str(content or "")))
                continue
            if role == "user":
                human_content = content
                if not isinstance(human_content, (str, list)):
                    human_content = str(human_content or "")
                built.append(HumanMessage(content=human_content))
                continue
            if role == "assistant":
                tool_calls: list[dict[str, Any]] = []
                for item in list(message.get("tool_calls") or []):
                    function_payload = item.get("function") if isinstance(item, dict) else {}
                    raw_arguments = function_payload.get("arguments") if isinstance(function_payload, dict) else {}
                    if isinstance(raw_arguments, str):
                        import json

                        try:
                            parsed_arguments = json.loads(raw_arguments or "{}")
                        except Exception:
                            parsed_arguments = {}
                    elif isinstance(raw_arguments, dict):
                        parsed_arguments = raw_arguments
                    else:
                        parsed_arguments = {}
                    tool_calls.append(
                        {
                            "id": str(item.get("id") or ""),
                            "name": str(function_payload.get("name") or ""),
                            "args": parsed_arguments,
                        }
                    )
                built.append(
                    AIMessage(
                        content=str(content or ""),
                        tool_calls=tool_calls,
                    )
                )
                continue
            if role == "tool":
                built.append(
                    ToolMessage(
                        content=str(content or ""),
                        tool_call_id=str(message.get("tool_call_id") or ""),
                    )
                )
        return built

    @staticmethod
    def _build_tools(tools: list[dict[str, Any]] | None) -> list[Any]:
        if not tools:
            return []

        built: list[Any] = []
        descriptions = {
            str(item.get("function", {}).get("name") or ""): str(
                item.get("function", {}).get("description") or ""
            )
            for item in tools
            if isinstance(item, dict)
        }
        if "terminal" in descriptions:
            built.append(
                StructuredTool.from_function(
                    func=_terminal_tool,
                    name="terminal",
                    description=descriptions["terminal"] or _terminal_tool.__doc__ or "",
                )
            )
        if "delegate_to_agent" in descriptions:
            built.append(
                StructuredTool.from_function(
                    func=_delegate_to_agent_tool,
                    name="delegate_to_agent",
                    description=descriptions["delegate_to_agent"] or _delegate_to_agent_tool.__doc__ or "",
                )
            )
        if "ask_user" in descriptions:
            built.append(
                StructuredTool.from_function(
                    func=_ask_user_tool,
                    name="ask_user",
                    description=descriptions["ask_user"] or _ask_user_tool.__doc__ or "",
                )
            )
        return built

    async def create_chat_completion(self, *, messages: list[dict], tools: list[dict] | None = None):
        llm = self.client
        bound_tools = self._build_tools(tools)
        if bound_tools and hasattr(llm, "bind_tools"):
            llm = llm.bind_tools(bound_tools)

        response = await llm.ainvoke(self._build_langchain_messages(messages))
        tool_calls = self._assistant_tool_calls_from_message(response)
        return {
            "content": self._serialize_ai_content(response),
            "tool_calls": tool_calls,
            "usage": getattr(response, "usage_metadata", None),
            "total_tokens": self._extract_total_tokens(response),
            "streamed": False,
        }

    async def stream_chat_completion(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ):
        response = await self.create_chat_completion(messages=messages, tools=tools)
        content = str(response.get("content") or "")
        if content and on_content_delta:
            await on_content_delta(content)
        response["streamed"] = True
        return response
