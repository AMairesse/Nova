from __future__ import annotations

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from unittest.mock import AsyncMock, Mock, patch

from nova.models.Provider import LLMProvider, ProviderType
from nova.providers import list_provider_models
from nova.providers.lmstudio import fetch_lmstudio_models


class ProviderCatalogTests(SimpleTestCase):
    def _provider(self, provider_type: str, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="Catalog Provider",
            provider_type=provider_type,
            model=kwargs.get("model", ""),
            api_key=kwargs.get("api_key", "dummy-secret"),
            base_url=kwargs.get("base_url"),
            max_context_tokens=4096,
        )

    @patch("nova.providers.openrouter.fetch_openrouter_model_catalog", new_callable=AsyncMock)
    def test_openrouter_catalog_items_include_rich_metadata(self, mocked_catalog):
        mocked_catalog.return_value = [
            {
                "id": "openai/gpt-4.1-mini",
                "name": "GPT-4.1 Mini",
                "description": "Fast model",
                "context_length": 128000,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "supported_parameters": ["tools", "reasoning"],
                "pricing": {"prompt": "0.10", "completion": "0.40"},
                "top_provider": {"context_length": 64000, "max_completion_tokens": 8192},
            }
        ]

        payload = async_to_sync(list_provider_models)(
            self._provider(ProviderType.OPENROUTER)
        )

        self.assertEqual(payload[0]["id"], "openai/gpt-4.1-mini")
        self.assertEqual(payload[0]["label"], "GPT-4.1 Mini")
        self.assertEqual(payload[0]["description"], "Fast model")
        self.assertEqual(payload[0]["suggested_max_context_tokens"], 64000)
        self.assertEqual(payload[0]["input_modalities"]["image"], "pass")
        self.assertEqual(payload[0]["operations"]["tools"], "pass")
        self.assertEqual(payload[0]["operations"]["reasoning"], "pass")
        self.assertEqual(payload[0]["pricing"]["prompt"], "0.10")

    @patch("nova.providers.lmstudio.fetch_lmstudio_models", new_callable=AsyncMock)
    def test_lmstudio_catalog_items_include_loaded_state_and_capabilities(self, mocked_models):
        mocked_models.return_value = [
            {
                "key": "model-b",
                "type": "llm",
                "display_name": "Model B",
                "description": "Not loaded",
                "max_context_length": 8192,
                "loaded_instances": [],
                "capabilities": {"vision": False, "trained_for_tool_use": False},
            },
            {
                "key": "model-a",
                "type": "llm",
                "display_name": "Model A",
                "description": "Loaded",
                "publisher": "lmstudio-community",
                "max_context_length": 16384,
                "loaded_instances": [{"identifier": "gpu-1"}],
                "capabilities": {"vision": True, "trained_for_tool_use": True},
            },
            {
                "key": "text-embedding-model",
                "type": "embeddings",
                "display_name": "Embedding model",
                "description": "Should be filtered out",
                "max_context_length": 4096,
                "loaded_instances": [],
                "capabilities": {},
            },
        ]

        payload = async_to_sync(list_provider_models)(
            self._provider(ProviderType.LLMSTUDIO, api_key="")
        )

        self.assertEqual(payload[0]["id"], "model-a")
        self.assertTrue(payload[0]["state"]["loaded"])
        self.assertEqual(payload[0]["input_modalities"]["image"], "pass")
        self.assertEqual(payload[0]["operations"]["tools"], "pass")
        self.assertEqual(payload[0]["provider_metadata"]["publisher"], "lmstudio-community")
        self.assertEqual(payload[0]["provider_metadata"]["model_key"], "model-a")
        self.assertEqual(payload[1]["state"]["loaded"], False)
        self.assertEqual(len(payload), 2)

    @patch("nova.providers.lmstudio.httpx.AsyncClient")
    def test_fetch_lmstudio_models_accepts_models_payload_shape(self, mocked_client_class):
        mocked_response = Mock()
        mocked_response.status_code = 200
        mocked_response.json.return_value = {
            "object": "list",
            "data": [],
            "models": [
                {"key": "model-a", "type": "llm"},
                {"key": "model-b", "type": "llm"},
            ],
        }
        mocked_client = AsyncMock()
        mocked_client.get.return_value = mocked_response
        mocked_client_class.return_value.__aenter__.return_value = mocked_client
        mocked_client_class.return_value.__aexit__.return_value = False

        payload = async_to_sync(fetch_lmstudio_models)("http://localhost:1234")

        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["key"], "model-a")

    def test_manual_provider_types_return_empty_catalog(self):
        payload = async_to_sync(list_provider_models)(
            self._provider(ProviderType.OPENAI, model="gpt-4.1-mini")
        )

        self.assertEqual(payload, [])
