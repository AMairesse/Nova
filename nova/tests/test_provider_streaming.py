from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from nova.models.Provider import LLMProvider, ProviderType
from nova.providers.ollama import OllamaProviderAdapter
from nova.providers.openai_compatible import stream_openai_compatible_chat
from nova.providers.openrouter import OpenRouterProviderAdapter


class _FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _iterate():
            for chunk in self._chunks:
                yield chunk

        return _iterate()


class ProviderStreamingTests(SimpleTestCase):
    def _provider(self, provider_type=ProviderType.OPENAI, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="Streaming Provider",
            provider_type=provider_type,
            model=kwargs.get("model", "gpt-4o-mini"),
            api_key=kwargs.get("api_key", "dummy-secret"),
            base_url=kwargs.get("base_url"),
        )

    def test_stream_openai_compatible_chat_aggregates_text_and_tool_calls(self):
        recorded_deltas: list[str] = []
        fake_stream = _FakeAsyncStream(
            [
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "weather_lookup",
                                            "arguments": '{"city"',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": ': "Paris"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    },
                },
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=fake_stream)
                )
            )
        )

        async def _record_delta(delta: str) -> None:
            recorded_deltas.append(delta)

        with patch(
            "nova.providers.openai_compatible.create_openai_compatible_client",
            return_value=fake_client,
        ):
            response = async_to_sync(stream_openai_compatible_chat)(
                model="gpt-4o-mini",
                api_key="dummy-secret",
                base_url="https://api.example.com/v1",
                messages=[{"role": "user", "content": "Hello"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "weather_lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                normalize_content=lambda content: content,
                on_content_delta=_record_delta,
            )

        fake_client.chat.completions.create.assert_awaited_once()
        self.assertEqual(recorded_deltas, ["Hel", "lo"])
        self.assertEqual(response["content"], "Hello")
        self.assertEqual(response["tool_calls"][0]["id"], "call_1")
        self.assertEqual(response["tool_calls"][0]["name"], "weather_lookup")
        self.assertEqual(response["tool_calls"][0]["arguments"], '{"city": "Paris"}')
        self.assertTrue(response["streamed"])
        self.assertEqual(response["streaming_mode"], "native")
        self.assertEqual(response["total_tokens"], 14)

    def test_openrouter_stream_chat_uses_normalized_base_url(self):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider(
            provider_type=ProviderType.OPENROUTER,
            model="openai/gpt-4.1-mini",
            base_url="https://openrouter.ai/api",
        )

        with patch(
            "nova.providers.openrouter.stream_openai_compatible_chat",
            new_callable=AsyncMock,
            return_value={
                "content": "Hello",
                "tool_calls": [],
                "streamed": True,
                "streaming_mode": "native",
            },
        ) as mocked_stream:
            async_to_sync(adapter.stream_chat)(
                provider,
                messages=[{"role": "user", "content": "Hello"}],
                tools=None,
                on_content_delta=None,
            )

        mocked_stream.assert_awaited_once_with(
            model="openai/gpt-4.1-mini",
            api_key="dummy-secret",
            base_url="https://openrouter.ai/api/v1",
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            normalize_content=adapter.normalize_multimodal_content,
            on_content_delta=None,
        )

    def test_ollama_stream_chat_requires_fallback_when_tools_are_enabled(self):
        adapter = OllamaProviderAdapter()

        with self.assertRaises(NotImplementedError):
            async_to_sync(adapter.stream_chat)(
                self._provider(provider_type=ProviderType.OLLAMA),
                messages=[{"role": "user", "content": "Hello"}],
                tools=[{"type": "function", "function": {"name": "echo"}}],
                on_content_delta=None,
            )
