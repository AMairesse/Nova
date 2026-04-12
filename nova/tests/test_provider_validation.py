from __future__ import annotations

import base64
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from unittest.mock import AsyncMock, patch

from nova.llm.provider_validation import (
    _VALIDATION_IMAGE_BASE64,
    _VALIDATION_PDF_BASE64,
    validate_provider_configuration,
)
from nova.models.Provider import LLMProvider, ProviderType


def _payload_part_types(messages) -> list[str]:
    payload = messages[0]["content"] if isinstance(messages, list) else messages
    if not isinstance(payload, list):
        return []
    return [
        str(part.get("type") or "").strip()
        for part in payload
        if isinstance(part, dict)
    ]


class _HappyAdapter:
    def __init__(self):
        self.invocations = []

    async def complete_chat(self, provider, *, messages, tools=None):
        self.invocations.append({"messages": messages, "tools": tools, "provider": provider})
        payload = messages[0]["content"] if isinstance(messages, list) else messages
        if tools:
            return {
                "content": "",
                "tool_calls": [{"id": "call_1", "name": "provider_validation_echo", "arguments": '{"value":"ok"}'}],
            }
        if isinstance(payload, list):
            part_types = _payload_part_types(messages)
            if "file" in part_types or "document_url" in part_types:
                return {"content": "pdf accepted", "tool_calls": []}
            return {"content": "small image", "tool_calls": []}
        return {"content": "OK", "tool_calls": []}

    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        del provider, messages, tools
        if on_content_delta:
            await on_content_delta("chunk")
        return {
            "content": "chunk",
            "tool_calls": [],
            "streamed": True,
            "streaming_mode": "native",
        }

    def supports_active_pdf_input_probe(self, provider) -> bool:
        return provider.provider_type != ProviderType.LLMSTUDIO

    def build_validation_pdf_content(self, provider, *, pdf_base64: str):
        del provider
        return [
            {
                "type": "text",
                "text": "Confirm that you can access the attached PDF. Reply in one short sentence.",
            },
            {
                "type": "file",
                "source_type": "base64",
                "data": pdf_base64,
                "mime_type": "application/pdf",
                "filename": "provider-validation.pdf",
            },
        ]


class _NoToolsAdapter(_HappyAdapter):
    async def complete_chat(self, provider, *, messages, tools=None):
        if tools:
            raise NotImplementedError("Tool calling is not supported")
        return await super().complete_chat(provider, messages=messages, tools=tools)


class _NoVisionAdapter(_HappyAdapter):
    async def complete_chat(self, provider, *, messages, tools=None):
        part_types = _payload_part_types(messages)
        if "image_url" in part_types or "image" in part_types:
            raise ValueError("Vision inputs are not supported")
        return await super().complete_chat(provider, messages=messages, tools=tools)


class _NoPdfAdapter(_HappyAdapter):
    async def complete_chat(self, provider, *, messages, tools=None):
        part_types = _payload_part_types(messages)
        if "file" in part_types or "document_url" in part_types:
            raise ValueError("PDF inputs are not supported")
        return await super().complete_chat(provider, messages=messages, tools=tools)


class _BrokenStreamingAdapter(_HappyAdapter):
    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        del provider, messages, tools, on_content_delta
        raise RuntimeError("Streaming not available")


class _FallbackStreamingAdapter(_HappyAdapter):
    async def stream_chat(self, provider, *, messages, tools=None, on_content_delta=None):
        del provider, messages, tools, on_content_delta
        return {
            "content": "chunk",
            "tool_calls": [],
            "streamed": False,
            "streaming_mode": "fallback",
        }


class ProviderValidationServiceTests(SimpleTestCase):
    def _provider(self, provider_type=ProviderType.OPENAI, **kwargs) -> LLMProvider:
        return LLMProvider(
            name="Validation Provider",
            provider_type=provider_type,
            model=kwargs.get("model", "gpt-4o-mini"),
            api_key=kwargs.get("api_key", "dummy"),
            base_url=kwargs.get("base_url"),
        )

    @patch("nova.providers.validation.get_provider_adapter", return_value=_HappyAdapter())
    def test_validate_provider_configuration_success(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["chat"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["streaming"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["tools"]["status"], "pass")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

    def test_validate_provider_configuration_requires_model(self):
        result = async_to_sync(validate_provider_configuration)(self._provider(model=""))

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("requires a selected model", result["verification_summary"])

    @patch("nova.providers.validation.get_provider_adapter", side_effect=RuntimeError("401 Unauthorized"))
    def test_validate_provider_configuration_marks_invalid_when_provider_creation_fails(
        self,
        _mock_get_provider_adapter,
    ):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.INVALID)
        self.assertIn("provider creation", result["verification_summary"])
        self.assertEqual(result["verified_operations"]["vision"]["status"], "fail")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_NoToolsAdapter())
    def test_validate_provider_configuration_marks_partial_without_tools(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["tools"]["status"], "unsupported")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_NoVisionAdapter())
    def test_validate_provider_configuration_marks_partial_without_vision(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["vision"]["status"], "unsupported")
        self.assertEqual(result["verified_operations"]["tools"]["status"], "pass")
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_BrokenStreamingAdapter())
    def test_validate_provider_configuration_marks_partial_without_streaming(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["streaming"]["status"], "unsupported")
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_FallbackStreamingAdapter())
    def test_validate_provider_configuration_rejects_synthetic_streaming(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_operations"]["streaming"]["status"], "unsupported")
        self.assertIn("non-native", result["verified_operations"]["streaming"]["message"])

    @patch("nova.providers.validation.get_provider_adapter", return_value=_NoPdfAdapter())
    def test_validate_provider_configuration_marks_partial_without_pdf_input(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(self._provider())

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "unsupported")
        self.assertEqual(result["verified_operations"]["vision"]["status"], "pass")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_HappyAdapter())
    def test_validate_provider_configuration_skips_active_pdf_probe_for_provider_types_without_strategy(
        self,
        _mock_get_provider_adapter,
    ):
        result = async_to_sync(validate_provider_configuration)(
            self._provider(provider_type=ProviderType.LLMSTUDIO)
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "not_run")

    @patch("nova.providers.validation.get_provider_adapter", return_value=_HappyAdapter())
    def test_validate_mistral_configuration_uses_document_url_for_pdf_probe(self, _mock_get_provider_adapter):
        result = async_to_sync(validate_provider_configuration)(
            self._provider(
                provider_type=ProviderType.MISTRAL,
                model="mistral-small-latest",
            )
        )

        self.assertEqual(result["validation_status"], LLMProvider.ValidationStatus.VALID)
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

        adapter = _mock_get_provider_adapter.return_value
        document_payloads = [
            invocation["messages"][0]["content"]
            for invocation in adapter.invocations
            if "document_url" in _payload_part_types(invocation["messages"])
        ]
        self.assertEqual(len(document_payloads), 1)
        self.assertTrue(
            document_payloads[0][1]["document_url"].startswith("data:application/pdf;base64,")
        )

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.get_provider_adapter", return_value=_HappyAdapter())
    def test_validate_openrouter_configuration_does_not_use_declared_metadata_during_active_verification(
        self,
        _mock_get_provider_adapter,
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
        self.assertEqual(result["verified_inputs"]["pdf"]["status"], "pass")

    @patch("nova.providers.openrouter.fetch_openrouter_model_metadata", new_callable=AsyncMock)
    @patch("nova.providers.validation.get_provider_adapter", return_value=_HappyAdapter())
    def test_validate_openai_configuration_does_not_use_openrouter_metadata_even_with_openrouter_url(
        self,
        _mock_get_provider_adapter,
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

    def test_validation_probe_pdf_fixture_is_a_valid_pdf(self):
        raw = base64.b64decode(_VALIDATION_PDF_BASE64)

        self.assertEqual(raw[:5], b"%PDF-")
        self.assertTrue(raw.rstrip().endswith(b"%%EOF"))
