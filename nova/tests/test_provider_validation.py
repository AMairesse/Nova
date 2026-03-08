from __future__ import annotations

import base64
import struct
import zlib

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from unittest.mock import AsyncMock, patch

from nova.providers.openrouter import (
    OpenRouterMetadataAuthError,
    OpenRouterMetadataTransientError,
)
from nova.llm.provider_validation import _VALIDATION_IMAGE_BASE64, validate_provider_configuration
from nova.models.Provider import LLMProvider, ProviderType


class _FakeResponse:
    def __init__(self, content="OK", *, tool_calls=None, additional_kwargs=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.additional_kwargs = additional_kwargs or {}


class _HappyToolLLM:
    async def ainvoke(self, _messages):
        return _FakeResponse(tool_calls=[{"name": "provider_validation_echo"}])


class _HappyLLM:
    async def ainvoke(self, messages):
        payload = messages[0].content if isinstance(messages, list) else messages
        if isinstance(payload, list):
            return _FakeResponse(content="small image")
        return _FakeResponse(content="OK")

    async def astream(self, _messages):
        yield _FakeResponse(content="chunk")

    def bind_tools(self, _tools):
        return _HappyToolLLM()


class _NoToolsLLM(_HappyLLM):
    def bind_tools(self, _tools):
        raise NotImplementedError("Tool calling is not supported")


class _NoVisionLLM(_HappyLLM):
    async def ainvoke(self, messages):
        payload = messages[0].content if isinstance(messages, list) else messages
        if isinstance(payload, list):
            raise ValueError("Vision inputs are not supported")
        return await super().ainvoke(messages)


class _BrokenStreamingLLM(_HappyLLM):
    async def astream(self, _messages):
        if False:
            yield None
        raise RuntimeError("Streaming not available")


class ProviderValidationServiceTests(SimpleTestCase):
    def _provider(self, provider_type=ProviderType.OPENAI, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="Validation Provider",
            provider_type=provider_type,
            model=kwargs.get("model", "gpt-4o-mini"),
            api_key=kwargs.get("api_key", "dummy"),
            base_url=kwargs.get("base_url"),
        )

    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_provider_configuration_success(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["chat"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["streaming"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "pass")

    def test_validate_provider_configuration_requires_model(self):
        result = async_to_sync(validate_provider_configuration)(self._provider(model=""))

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("requires a selected model", result["validation_summary"])

    @patch("nova.providers.validation.create_provider_llm", side_effect=RuntimeError("401 Unauthorized"))
    def test_validate_provider_configuration_marks_invalid_when_provider_creation_fails(
        self,
        _mock_create_provider_llm,
    ):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("provider creation", result["validation_summary"])
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "fail")

    @patch("nova.providers.validation.create_provider_llm", return_value=_NoToolsLLM())
    def test_validate_provider_configuration_marks_partial_without_tools(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "unsupported")
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "pass")

    @patch("nova.providers.validation.create_provider_llm", return_value=_NoVisionLLM())
    def test_validate_provider_configuration_marks_partial_without_vision(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "unsupported")
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "pass")

    @patch("nova.providers.validation.create_provider_llm", return_value=_BrokenStreamingLLM())
    def test_validate_provider_configuration_marks_partial_without_streaming(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["streaming"]["status"], "unsupported")

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openrouter_configuration_uses_declared_metadata_for_tools_and_vision(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        mocked_metadata.return_value = {
            "id": "google/gemini-2.5-flash",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["tools", "tool_choice"],
        }

        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENROUTER,
                model="google/gemini-2.5-flash",
            )
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["tools"]["source"], "metadata")
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["vision"]["source"], "metadata")
        self.assertIn("OpenRouter model metadata was used", result["validation_summary"])

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openrouter_configuration_marks_vision_unsupported_from_metadata(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        mocked_metadata.return_value = {
            "id": "anthropic/claude-3-haiku",
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["tools"],
        }

        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENROUTER,
                model="anthropic/claude-3-haiku",
            )
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "unsupported")
        self.assertEqual(result["validation_capabilities"]["vision"]["source"], "metadata")

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openrouter_configuration_falls_back_to_probes_when_metadata_is_unavailable(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        mocked_metadata.side_effect = OpenRouterMetadataTransientError("catalog timeout")

        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENROUTER,
                model="google/gemini-2.5-flash",
            )
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["validation_capabilities"]["tools"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["tools"]["source"], "probe")
        self.assertEqual(result["validation_capabilities"]["vision"]["status"], "pass")
        self.assertEqual(result["validation_capabilities"]["vision"]["source"], "probe")
        self.assertIn("active probes were used", result["validation_summary"])

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openrouter_configuration_marks_invalid_when_metadata_auth_fails(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        mocked_metadata.side_effect = OpenRouterMetadataAuthError("invalid API key")

        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENROUTER,
                model="google/gemini-2.5-flash",
            )
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("OpenRouter metadata lookup", result["validation_summary"])

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openai_configuration_does_not_use_openrouter_metadata_even_with_openrouter_url(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENAI,
                base_url="https://openrouter.ai/api/v1",
            )
        )

        mocked_metadata.assert_not_called()
        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)

    def test_validation_probe_image_fixture_is_a_valid_png(self):
        raw = base64.b64decode(_VALIDATION_IMAGE_BASE64)

        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

        pos = 8
        saw_idat = False
        while pos < len(raw):
            chunk_length = struct.unpack(">I", raw[pos:pos + 4])[0]
            chunk_type = raw[pos + 4:pos + 8]
            chunk_data = raw[pos + 8:pos + 8 + chunk_length]
            if chunk_type == b"IDAT":
                zlib.decompress(chunk_data)
                saw_idat = True
            pos += 12 + chunk_length

        self.assertTrue(saw_idat)
