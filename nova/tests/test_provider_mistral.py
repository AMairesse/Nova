from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from nova.models.Provider import LLMProvider, ProviderType
from nova.providers.mistral import (
    MISTRAL_DEFAULT_BASE_URL,
    MistralProviderAdapter,
    build_mistral_capability_snapshot,
    fetch_mistral_model_catalog,
    get_mistral_base_url,
    get_mistral_models_url,
)


class ResponseStub:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        json_error: Exception | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeAsyncStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        async def _iterate():
            for event in self._events:
                yield event

        return _iterate()


class MistralProviderTests(SimpleTestCase):
    def _provider(self, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="Mistral Test Provider",
            provider_type=ProviderType.MISTRAL,
            model=kwargs.get("model", "mistral-small-latest"),
            api_key=kwargs.get("api_key", "dummy-secret"),
            base_url=kwargs.get("base_url"),
            max_context_tokens=4096,
        )

    def test_mistral_url_helpers_normalize_default_and_custom_urls(self):
        self.assertEqual(get_mistral_base_url(None), MISTRAL_DEFAULT_BASE_URL)
        self.assertEqual(
            get_mistral_base_url("https://api.mistral.ai/v1/"),
            "https://api.mistral.ai/v1",
        )
        self.assertEqual(
            get_mistral_models_url(None),
            "https://api.mistral.ai/v1/models",
        )
        self.assertEqual(
            get_mistral_models_url("https://proxy.example/custom/v1"),
            "https://proxy.example/custom/v1/models",
        )

    def test_build_mistral_capability_snapshot_maps_chat_model_capabilities(self):
        snapshot = build_mistral_capability_snapshot(
            {
                "id": "mistral-small-latest",
                "name": "Mistral Small Latest",
                "max_context_length": 131072,
                "capabilities": {
                    "completion_chat": True,
                    "function_calling": True,
                    "vision": False,
                },
            }
        )

        self.assertEqual(snapshot["metadata_source_label"], "Mistral models API")
        self.assertEqual(snapshot["inputs"]["text"], "pass")
        self.assertEqual(snapshot["inputs"]["pdf"], "pass")
        self.assertEqual(snapshot["inputs"]["image"], "unsupported")
        self.assertEqual(snapshot["operations"]["chat"], "pass")
        self.assertEqual(snapshot["operations"]["tools"], "pass")
        self.assertEqual(snapshot["operations"]["vision"], "unsupported")
        self.assertEqual(snapshot["limits"]["context_tokens"], 131072)

    @patch("nova.providers.mistral.fetch_mistral_model_catalog", new_callable=AsyncMock)
    def test_adapter_list_models_filters_non_chat_models(self, mocked_catalog):
        mocked_catalog.return_value = [
            {
                "id": "mistral-small-latest",
                "name": "Mistral Small Latest",
                "max_context_length": 131072,
                "capabilities": {
                    "completion_chat": True,
                    "function_calling": True,
                    "vision": True,
                },
            },
            {
                "id": "mistral-embed",
                "name": "Mistral Embed",
                "max_context_length": 8192,
                "capabilities": {
                    "completion_chat": False,
                },
            },
        ]

        payload = async_to_sync(MistralProviderAdapter().list_models)(self._provider())

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], "mistral-small-latest")
        self.assertEqual(payload[0]["input_modalities"]["pdf"], "pass")
        self.assertEqual(payload[0]["operations"]["tools"], "pass")

    @patch("nova.providers.mistral.fetch_mistral_model_catalog", new_callable=AsyncMock)
    def test_adapter_resolve_snapshot_matches_alias(self, mocked_catalog):
        mocked_catalog.return_value = [
            {
                "id": "mistral-small-2503",
                "name": "Mistral Small 25.03",
                "aliases": ["mistral-small-latest"],
                "max_context_length": 131072,
                "capabilities": {
                    "completion_chat": True,
                    "function_calling": False,
                    "vision": True,
                },
            }
        ]

        snapshot = async_to_sync(MistralProviderAdapter().resolve_capability_snapshot)(
            self._provider(model="mistral-small-latest")
        )

        self.assertEqual(snapshot["inputs"]["pdf"], "pass")
        self.assertEqual(snapshot["operations"]["tools"], "unsupported")
        self.assertEqual(snapshot["operations"]["vision"], "pass")

    @patch("nova.providers.mistral.assert_allowed_egress_url_sync")
    @patch("nova.providers.mistral.Mistral")
    def test_adapter_stream_chat_returns_native_streaming_payload(self, mocked_mistral, _mocked_policy):
        client = Mock()
        client.chat.stream_async = AsyncMock(
            return_value=_FakeAsyncStream(
                [
                    SimpleNamespace(data={"choices": [{"delta": {"content": "Bon"}}]}),
                    SimpleNamespace(
                        data={
                            "choices": [{"delta": {"content": "jour"}}],
                            "usage": {
                                "prompt_tokens": 9,
                                "completion_tokens": 2,
                                "total_tokens": 11,
                            },
                        }
                    ),
                ]
            )
        )
        mocked_mistral.return_value = client

        streamed_deltas: list[str] = []

        async def _record_delta(delta: str) -> None:
            streamed_deltas.append(delta)

        response = async_to_sync(MistralProviderAdapter().stream_chat)(
            self._provider(),
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            on_content_delta=_record_delta,
        )

        client.chat.stream_async.assert_awaited_once()
        self.assertEqual(streamed_deltas, ["Bon", "jour"])
        self.assertEqual(response["content"], "Bonjour")
        self.assertTrue(response["streamed"])
        self.assertEqual(response["streaming_mode"], "native")
        self.assertEqual(response["total_tokens"], 11)

    @patch("nova.providers.mistral.safe_http_request", new_callable=AsyncMock)
    def test_fetch_mistral_model_catalog_accepts_data_payload_shape(self, mocked_request):
        mocked_request.return_value = ResponseStub(
            payload={
            "object": "list",
            "data": [
                {"id": "mistral-small-latest", "capabilities": {"completion_chat": True}},
                {"id": "mistral-large-latest", "capabilities": {"completion_chat": True}},
            ],
            }
        )

        payload = async_to_sync(fetch_mistral_model_catalog)("dummy-secret", None)

        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["id"], "mistral-small-latest")

    @patch("nova.providers.mistral.safe_http_request", new_callable=AsyncMock)
    def test_fetch_mistral_model_catalog_maps_timeout_and_http_errors(self, mocked_request):
        for error in [
            httpx.TimeoutException("timeout"),
            httpx.HTTPError("boom"),
        ]:
            with self.subTest(error=type(error).__name__):
                mocked_request.side_effect = error

                with self.assertRaises(RuntimeError):
                    async_to_sync(fetch_mistral_model_catalog)("dummy-secret", None)

                mocked_request.reset_mock()
                mocked_request.side_effect = None

    def test_fetch_mistral_model_catalog_requires_api_key(self):
        with self.assertRaises(RuntimeError):
            async_to_sync(fetch_mistral_model_catalog)("", None)
