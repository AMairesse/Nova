from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from nova.models.Provider import LLMProvider, ProviderType
from nova.providers.openrouter import (
    OpenRouterMetadataAuthError,
    OpenRouterMetadataError,
    OpenRouterMetadataTransientError,
    OpenRouterModelNotFoundError,
    OpenRouterProviderAdapter,
    build_openrouter_capability_snapshot,
    build_openrouter_catalog_item,
    fetch_openrouter_model_catalog,
    fetch_openrouter_model_metadata,
    get_openrouter_base_url,
    get_openrouter_models_url,
    is_openrouter_base_url,
    parse_openrouter_declared_capabilities,
)


class ResponseStub:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        json_error: Exception | None = None,
        text: str = "",
    ):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def configure_async_client(
    mocked_client_class,
    *,
    get_response=None,
    get_error: Exception | None = None,
    post_response=None,
    post_error: Exception | None = None,
):
    mocked_client = AsyncMock()
    if get_error is not None:
        mocked_client.get.side_effect = get_error
    elif get_response is not None:
        mocked_client.get.return_value = get_response

    if post_error is not None:
        mocked_client.post.side_effect = post_error
    elif post_response is not None:
        mocked_client.post.return_value = post_response

    mocked_client_class.return_value.__aenter__.return_value = mocked_client
    mocked_client_class.return_value.__aexit__.return_value = False
    return mocked_client


class OpenRouterProviderTests(SimpleTestCase):
    def _provider(self, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="OpenRouter Test Provider",
            provider_type=ProviderType.OPENROUTER,
            model=kwargs.get("model", "openai/gpt-4.1-mini"),
            api_key=kwargs.get("api_key", "dummy-secret"),
            base_url=kwargs.get("base_url"),
            additional_config=kwargs.get("additional_config", {}),
            max_context_tokens=4096,
        )

    def test_openrouter_url_helpers_normalize_canonical_and_custom_urls(self):
        self.assertFalse(is_openrouter_base_url(None))
        self.assertTrue(is_openrouter_base_url("https://openrouter.ai"))
        self.assertTrue(is_openrouter_base_url("https://openrouter.ai/api"))
        self.assertTrue(is_openrouter_base_url("https://openrouter.ai/api/v1/"))
        self.assertFalse(is_openrouter_base_url("https://example.com/api/v1"))

        self.assertEqual(
            get_openrouter_base_url(None),
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(
            get_openrouter_base_url("https://openrouter.ai/api"),
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(
            get_openrouter_base_url("https://proxy.example/v2/"),
            "https://proxy.example/v2",
        )
        self.assertEqual(
            get_openrouter_models_url("https://openrouter.ai"),
            "https://openrouter.ai/api/v1/models",
        )

    def test_parse_declared_capabilities_handles_architecture_and_fallback_inputs(self):
        self.assertEqual(
            parse_openrouter_declared_capabilities(
                {
                    "architecture": {"input_modalities": ["text", "image"]},
                    "supported_parameters": ["parallel_tool_calls"],
                }
            ),
            {"vision": True, "tools": True},
        )
        self.assertEqual(
            parse_openrouter_declared_capabilities(
                {
                    "input_modalities": ["text"],
                    "supported_parameters": "not-a-list",
                }
            ),
            {"vision": False, "tools": None},
        )

    def test_build_openrouter_catalog_item_uses_top_level_modalities_and_empty_pricing(self):
        item = build_openrouter_catalog_item(
            {
                "id": "anthropic/claude-vision",
                "name": "Claude Vision",
                "description": "Vision capable.",
                "context_length": "64000",
                "input_modalities": ["text", "pdf", "audio"],
                "output_modalities": ["text", "image"],
                "supported_parameters": ["response_format"],
                "pricing": "invalid",
            }
        )

        self.assertEqual(item["id"], "anthropic/claude-vision")
        self.assertEqual(item["label"], "Claude Vision")
        self.assertEqual(item["context_length"], 64000)
        self.assertEqual(item["suggested_max_context_tokens"], 64000)
        self.assertEqual(item["input_modalities"]["pdf"], "pass")
        self.assertEqual(item["input_modalities"]["audio"], "pass")
        self.assertEqual(item["output_modalities"]["image"], "pass")
        self.assertEqual(item["operations"]["tools"], "unsupported")
        self.assertEqual(item["operations"]["structured_output"], "pass")
        self.assertEqual(item["pricing"], {})

    def test_build_openrouter_capability_snapshot_includes_limits_and_operation_flags(self):
        snapshot = build_openrouter_capability_snapshot(
            {
                "id": "x-ai/grok-audio",
                "name": "Grok Audio",
                "context_length": 128000,
                "architecture": {
                    "input_modalities": ["text", "file"],
                    "output_modalities": ["text", "audio"],
                },
                "supported_parameters": ["tool_choice", "reasoning"],
                "top_provider": {"max_completion_tokens": 4096},
            }
        )

        self.assertEqual(snapshot["metadata_source_label"], "OpenRouter models API")
        self.assertEqual(snapshot["inputs"]["pdf"], "pass")
        self.assertEqual(snapshot["inputs"]["image"], "unsupported")
        self.assertEqual(snapshot["outputs"]["audio"], "pass")
        self.assertEqual(snapshot["operations"]["tools"], "pass")
        self.assertEqual(snapshot["operations"]["reasoning"], "pass")
        self.assertEqual(snapshot["operations"]["image_generation"], "unsupported")
        self.assertEqual(snapshot["limits"]["context_tokens"], 128000)
        self.assertEqual(snapshot["limits"]["max_completion_tokens"], 4096)

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_fetch_openrouter_model_metadata_returns_match_by_canonical_slug(
        self,
        mocked_client_class,
    ):
        configure_async_client(
            mocked_client_class,
            get_response=ResponseStub(
                payload={
                    "data": [
                        {"id": "openai/gpt-4.1-mini", "canonical_slug": "gpt-4.1-mini"}
                    ]
                }
            ),
        )

        payload = async_to_sync(fetch_openrouter_model_metadata)(
            "secret",
            "gpt-4.1-mini",
            "https://openrouter.ai",
        )

        self.assertEqual(payload["id"], "openai/gpt-4.1-mini")

    def test_fetch_openrouter_model_metadata_requires_api_key(self):
        with self.assertRaises(OpenRouterMetadataAuthError):
            async_to_sync(fetch_openrouter_model_metadata)("", "model", None)

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_fetch_openrouter_model_metadata_maps_timeout_and_http_errors(
        self,
        mocked_client_class,
    ):
        for error in [
            httpx.TimeoutException("timeout"),
            httpx.HTTPError("boom"),
        ]:
            with self.subTest(error=type(error).__name__):
                configure_async_client(
                    mocked_client_class,
                    get_error=error,
                )
                with self.assertRaises(OpenRouterMetadataTransientError):
                    async_to_sync(fetch_openrouter_model_metadata)(
                        "secret",
                        "model",
                        None,
                    )
                mocked_client_class.reset_mock()

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_fetch_openrouter_model_metadata_maps_response_errors(
        self,
        mocked_client_class,
    ):
        cases = [
            (
                ResponseStub(status_code=401, payload={}),
                OpenRouterMetadataAuthError,
            ),
            (
                ResponseStub(status_code=500, payload={}),
                OpenRouterMetadataTransientError,
            ),
            (
                ResponseStub(status_code=200, json_error=ValueError("bad json")),
                OpenRouterMetadataTransientError,
            ),
            (
                ResponseStub(status_code=200, payload={"data": "bad-shape"}),
                OpenRouterMetadataTransientError,
            ),
            (
                ResponseStub(status_code=200, payload={"data": [{"id": "other-model"}]}),
                OpenRouterModelNotFoundError,
            ),
        ]

        for response, expected_error in cases:
            with self.subTest(error=expected_error.__name__, status=response.status_code):
                configure_async_client(mocked_client_class, get_response=response)
                with self.assertRaises(expected_error):
                    async_to_sync(fetch_openrouter_model_metadata)(
                        "secret",
                        "missing-model",
                        None,
                    )
                mocked_client_class.reset_mock()

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_fetch_openrouter_model_catalog_returns_only_dict_items(self, mocked_client_class):
        configure_async_client(
            mocked_client_class,
            get_response=ResponseStub(
                payload=[
                    {"id": "model-a"},
                    "skip-me",
                    {"id": "model-b"},
                ]
            ),
        )

        payload = async_to_sync(fetch_openrouter_model_catalog)(
            "secret",
            "https://openrouter.ai/api",
        )

        self.assertEqual(payload, [{"id": "model-a"}, {"id": "model-b"}])

    def test_fetch_openrouter_model_catalog_requires_api_key(self):
        with self.assertRaises(OpenRouterMetadataAuthError):
            async_to_sync(fetch_openrouter_model_catalog)("", None)

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_fetch_openrouter_model_catalog_maps_errors(self, mocked_client_class):
        cases = [
            (
                {"get_error": httpx.TimeoutException("timeout")},
                OpenRouterMetadataTransientError,
            ),
            (
                {"get_response": ResponseStub(status_code=403, payload={})},
                OpenRouterMetadataAuthError,
            ),
            (
                {"get_response": ResponseStub(status_code=502, payload={})},
                OpenRouterMetadataTransientError,
            ),
            (
                {
                    "get_response": ResponseStub(
                        status_code=200,
                        json_error=ValueError("bad json"),
                    )
                },
                OpenRouterMetadataTransientError,
            ),
            (
                {"get_response": ResponseStub(status_code=200, payload={"data": "bad"})},
                OpenRouterMetadataTransientError,
            ),
        ]

        for kwargs, expected_error in cases:
            with self.subTest(error=expected_error.__name__, kwargs=kwargs):
                configure_async_client(mocked_client_class, **kwargs)
                with self.assertRaises(expected_error):
                    async_to_sync(fetch_openrouter_model_catalog)("secret", None)
                mocked_client_class.reset_mock()

    @patch("nova.providers.openrouter.create_openai_compatible_llm")
    def test_adapter_create_llm_uses_normalized_base_url(self, mocked_create_llm):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider(base_url="https://openrouter.ai/api")

        adapter.create_llm(provider)

        mocked_create_llm.assert_called_once_with(
            model="openai/gpt-4.1-mini",
            api_key="dummy-secret",
            base_url="https://openrouter.ai/api/v1",
        )

    @patch("nova.providers.openrouter.fetch_openrouter_model_catalog", new_callable=AsyncMock)
    def test_adapter_list_models_filters_items_without_ids(self, mocked_catalog):
        mocked_catalog.return_value = [
            {"id": "model-a", "name": "Model A"},
            {"name": "Missing id"},
        ]
        adapter = OpenRouterProviderAdapter()

        payload = async_to_sync(adapter.list_models)(self._provider())

        self.assertEqual([item["id"] for item in payload], ["model-a"])

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    def test_adapter_resolve_snapshot_and_declared_capabilities_delegate_to_metadata(
        self,
        mocked_metadata,
    ):
        mocked_metadata.return_value = {
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "supported_parameters": ["tools"],
            "context_length": 64000,
            "top_provider": {},
        }
        adapter = OpenRouterProviderAdapter()
        provider = self._provider(model="openai/gpt-4.1-mini")

        snapshot = async_to_sync(adapter.resolve_capability_snapshot)(provider)
        declared = async_to_sync(adapter.fetch_declared_capabilities)(provider)

        self.assertEqual(snapshot["inputs"]["image"], "pass")
        self.assertEqual(snapshot["operations"]["tools"], "pass")
        self.assertEqual(declared, {"vision": True, "tools": True})
        self.assertEqual(mocked_metadata.await_count, 2)

    def test_adapter_build_native_request_supports_image_response_mode(self):
        adapter = OpenRouterProviderAdapter()
        adapter.normalize_multimodal_content = Mock(
            return_value=[{"type": "normalized"}]
        )
        provider = self._provider(
            additional_config={
                "image_generation": {"size": "1024x1024", "quality": "high"}
            }
        )

        payload = async_to_sync(adapter.build_native_request)(
            provider,
            {
                "prompt": "Generate a poster",
                "response_mode": "image",
                "artifacts": [
                    {
                        "kind": "image",
                        "data": "image-data",
                        "mime_type": "image/png",
                        "filename": "input.png",
                    },
                    {
                        "kind": "pdf",
                        "data": "pdf-data",
                        "mime_type": "application/pdf",
                        "label": "brief.pdf",
                    },
                    {
                        "kind": "audio",
                        "data": "audio-data",
                        "mime_type": "audio/mpeg",
                    },
                    {"kind": "image", "data": ""},
                    "skip-me",
                ],
            },
        )

        adapter.normalize_multimodal_content.assert_called_once_with(
            [
                {"type": "text", "text": "Generate a poster"},
                {
                    "type": "image",
                    "source_type": "base64",
                    "data": "image-data",
                    "mime_type": "image/png",
                    "filename": "input.png",
                },
                {
                    "type": "file",
                    "source_type": "base64",
                    "data": "pdf-data",
                    "mime_type": "application/pdf",
                    "filename": "brief.pdf",
                },
                {
                    "type": "audio",
                    "source_type": "base64",
                    "data": "audio-data",
                    "mime_type": "audio/mpeg",
                    "filename": "attachment",
                },
            ]
        )
        self.assertEqual(payload["model"], provider.model)
        self.assertEqual(payload["messages"][0]["content"], [{"type": "normalized"}])
        self.assertEqual(payload["modalities"], ["text", "image"])
        self.assertEqual(payload["size"], "1024x1024")
        self.assertEqual(payload["quality"], "high")

    def test_adapter_build_native_request_supports_audio_response_mode(self):
        adapter = OpenRouterProviderAdapter()
        adapter.normalize_multimodal_content = Mock(return_value="normalized-audio")
        provider = self._provider(
            additional_config={"audio": {"voice": "alloy", "format": "mp3"}}
        )

        payload = async_to_sync(adapter.build_native_request)(
            provider,
            {"prompt": "Read this aloud", "response_mode": "audio"},
        )

        self.assertEqual(payload["messages"][0]["content"], "normalized-audio")
        self.assertEqual(payload["modalities"], ["text", "audio"])
        self.assertEqual(payload["audio"], {"voice": "alloy", "format": "mp3"})

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_adapter_invoke_native_posts_payload_and_returns_json(self, mocked_client_class):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider(base_url="https://openrouter.ai/api")
        adapter.build_native_request = AsyncMock(return_value={"prompt": "payload"})
        mocked_client = configure_async_client(
            mocked_client_class,
            post_response=ResponseStub(payload={"choices": []}),
        )

        payload = async_to_sync(adapter.invoke_native)(provider, {"prompt": "ignored"})

        self.assertEqual(payload, {"choices": []})
        adapter.build_native_request.assert_awaited_once()
        mocked_client.post.assert_awaited_once_with(
            "https://openrouter.ai/api/v1/chat/completions",
            json={"prompt": "payload"},
        )

    @patch("nova.providers.openrouter.httpx.AsyncClient")
    def test_adapter_invoke_native_maps_error_responses(self, mocked_client_class):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider()
        adapter.build_native_request = AsyncMock(return_value={"payload": "ok"})

        cases = [
            (
                ResponseStub(status_code=401, payload={}),
                OpenRouterMetadataAuthError,
            ),
            (
                ResponseStub(status_code=500, payload={}, text="boom"),
                OpenRouterMetadataError,
            ),
            (
                ResponseStub(status_code=200, json_error=ValueError("bad json")),
                OpenRouterMetadataTransientError,
            ),
        ]

        for response, expected_error in cases:
            with self.subTest(error=expected_error.__name__, status=response.status_code):
                configure_async_client(
                    mocked_client_class,
                    post_response=response,
                )
                with self.assertRaises(expected_error):
                    async_to_sync(adapter.invoke_native)(provider, {"prompt": "ignored"})
                mocked_client_class.reset_mock()

    def test_extract_image_payload_supports_multiple_shapes(self):
        adapter = OpenRouterProviderAdapter()

        self.assertEqual(
            adapter._extract_image_payload("  data:image/png;base64,abc  "),
            ("  data:image/png;base64,abc  ", "", ""),
        )
        self.assertEqual(adapter._extract_image_payload(123), ("", "", ""))
        self.assertEqual(
            adapter._extract_image_payload(
                {
                    "image_url": "https://cdn.example/image.png",
                    "mime_type": "image/png",
                    "filename": "image.png",
                }
            ),
            ("https://cdn.example/image.png", "image/png", "image.png"),
        )
        self.assertEqual(
            adapter._extract_image_payload(
                {
                    "data": "base64-image",
                    "media_type": "image/webp",
                    "filename": "image.webp",
                }
            ),
            ("base64-image", "image/webp", "image.webp"),
        )

    def test_parse_native_response_handles_message_image_fallback(self):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider()

        parsed = async_to_sync(adapter.parse_native_response)(
            provider,
            {
                "choices": [
                    {
                        "message": {
                            "content": "Rendered text",
                            "image": {
                                "image_url": "https://cdn.example/result.png",
                                "mime_type": "image/png",
                                "filename": "result.png",
                            },
                        }
                    }
                ],
                "annotations": [{"kind": "notice"}],
                "audio": {"voice": "alloy"},
            },
        )

        self.assertEqual(parsed["text"], "Rendered text")
        self.assertEqual(parsed["annotations"], [{"kind": "notice"}])
        self.assertEqual(parsed["audio"], {"voice": "alloy"})
        self.assertEqual(
            parsed["images"],
            [
                {
                    "data": "https://cdn.example/result.png",
                    "mime_type": "image/png",
                    "filename": "result.png",
                }
            ],
        )

    def test_parse_native_response_ignores_invalid_entries_and_uses_raw_images(self):
        adapter = OpenRouterProviderAdapter()
        provider = self._provider()

        parsed = async_to_sync(adapter.parse_native_response)(
            provider,
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                None,
                                {"type": "text", "text": "Alpha"},
                                {"type": "output_image", "data": ""},
                            ]
                        }
                    }
                ],
                "images": [
                    {"data": "", "mime_type": "image/jpeg"},
                    {
                        "image_url": {
                            "url": "data:image/png;base64,abc",
                            "media_type": "image/png",
                        }
                    },
                ],
            },
        )

        self.assertEqual(parsed["text"], "Alpha")
        self.assertEqual(
            parsed["images"],
            [
                {
                    "data": "data:image/png;base64,abc",
                    "mime_type": "image/png",
                    "filename": "",
                }
            ],
        )
