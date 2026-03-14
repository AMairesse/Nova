from __future__ import annotations

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

    @patch("nova.providers.mistral.httpx.AsyncClient")
    def test_fetch_mistral_model_catalog_accepts_data_payload_shape(self, mocked_client_class):
        mocked_response = Mock()
        mocked_response.status_code = 200
        mocked_response.json.return_value = {
            "object": "list",
            "data": [
                {"id": "mistral-small-latest", "capabilities": {"completion_chat": True}},
                {"id": "mistral-large-latest", "capabilities": {"completion_chat": True}},
            ],
        }
        mocked_client = AsyncMock()
        mocked_client.get.return_value = mocked_response
        mocked_client_class.return_value.__aenter__.return_value = mocked_client
        mocked_client_class.return_value.__aexit__.return_value = False

        payload = async_to_sync(fetch_mistral_model_catalog)("dummy-secret", None)

        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["id"], "mistral-small-latest")

    @patch("nova.providers.mistral.httpx.AsyncClient")
    def test_fetch_mistral_model_catalog_maps_timeout_and_http_errors(self, mocked_client_class):
        for error in [
            httpx.TimeoutException("timeout"),
            httpx.HTTPError("boom"),
        ]:
            with self.subTest(error=type(error).__name__):
                mocked_client = AsyncMock()
                mocked_client.get.side_effect = error
                mocked_client_class.return_value.__aenter__.return_value = mocked_client
                mocked_client_class.return_value.__aexit__.return_value = False

                with self.assertRaises(RuntimeError):
                    async_to_sync(fetch_mistral_model_catalog)("dummy-secret", None)

                mocked_client_class.reset_mock()

    def test_fetch_mistral_model_catalog_requires_api_key(self):
        with self.assertRaises(RuntimeError):
            async_to_sync(fetch_mistral_model_catalog)("", None)

