from __future__ import annotations

import base64
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from unittest.mock import AsyncMock, patch

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
        self.assertEqual(result["verified_operations"]["chat"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["streaming"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["tools"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")

    def test_validate_provider_configuration_requires_model(self):
        result = async_to_sync(validate_provider_configuration)(self._provider(model=""))

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("requires a selected model", result["verification_summary"])

    @patch("nova.providers.validation.create_provider_llm", side_effect=RuntimeError("401 Unauthorized"))
    def test_validate_provider_configuration_marks_invalid_when_provider_creation_fails(
        self,
        _mock_create_provider_llm,
    ):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("provider creation", result["verification_summary"])
        self.assertEqual(result["verified_operations"]["vision"]["status"], "fail")

    @patch("nova.providers.validation.create_provider_llm", return_value=_NoToolsLLM())
    def test_validate_provider_configuration_marks_partial_without_tools(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["tools"]["status"], "unsupported")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")

    @patch("nova.providers.validation.create_provider_llm", return_value=_NoVisionLLM())
    def test_validate_provider_configuration_marks_partial_without_vision(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["vision"]["status"], "unsupported")
        self.assertEqual(result["verified_operations"]["tools"]["status"], "pass")

    @patch("nova.providers.validation.create_provider_llm", return_value=_BrokenStreamingLLM())
    def test_validate_provider_configuration_marks_partial_without_streaming(self, _mock_create_provider_llm):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["streaming"]["status"], "unsupported")

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.create_provider_llm", return_value=_HappyLLM())
    def test_validate_openrouter_configuration_does_not_use_declared_metadata_during_active_verification(
        self,
        _mock_create_provider_llm,
        mocked_metadata,
    ):
        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.OPENROUTER,
                model="google/gemini-2.5-flash",
            )
        )

        mocked_metadata.assert_not_called()
        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["tools"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["tools"]["source"], "probe")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["vision"]["source"], "probe")

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

    def test_validation_probe_image_fixture_is_a_valid_jpeg(self):
        raw = base64.b64decode(_VALIDATION_IMAGE_BASE64)

        self.assertEqual(raw[:2], b"\xff\xd8")
        self.assertEqual(raw[-2:], b"\xff\xd9")
