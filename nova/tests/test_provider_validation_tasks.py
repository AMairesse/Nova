from __future__ import annotations

from unittest.mock import AsyncMock, patch

from nova.models.Provider import LLMProvider, ProviderType
from nova.tasks.provider_validation_tasks import validate_provider_configuration_task
from nova.tests.base import BaseTestCase


def _validation_result() -> dict:
    return {
        "validation_status": LLMProvider.ValidationStatus.VALID,
        "validation_summary": "Validated successfully for chat, streaming, tools, and vision.",
        "validation_capabilities": {
            "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
            "streaming": {"status": "pass", "message": "ok", "latency_ms": 12},
            "tools": {"status": "pass", "message": "ok", "latency_ms": 14},
            "vision": {"status": "pass", "message": "ok", "latency_ms": 16},
        },
    }


class ProviderValidationTaskTests(BaseTestCase):
    def test_validation_task_applies_result_when_request_is_current(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Background Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )
        fingerprint = provider.compute_validation_fingerprint()
        provider.mark_validation_started(
            task_id="task-123",
            requested_fingerprint=fingerprint,
        )

        with patch(
            "nova.tasks.provider_validation_tasks.validate_provider_configuration",
            new_callable=AsyncMock,
        ) as mocked_validate:
            mocked_validate.return_value = _validation_result()
            validate_provider_configuration_task.apply(
                args=[provider.pk, fingerprint],
                task_id="task-123",
            )

        provider.refresh_from_db()
        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.VALID)
        self.assertEqual(provider.validation_task_id, "")
        self.assertEqual(provider.validation_requested_fingerprint, "")
        self.assertEqual(provider.validation_summary, _validation_result()["validation_summary"])

    def test_validation_task_is_skipped_when_provider_configuration_changed(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Background Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="secret-a",
        )
        fingerprint = provider.compute_validation_fingerprint()
        provider.mark_validation_started(
            task_id="task-123",
            requested_fingerprint=fingerprint,
        )
        provider.model = "gpt-4.1-mini"
        provider.save(update_fields=["model"])

        with patch(
            "nova.tasks.provider_validation_tasks.validate_provider_configuration",
            new_callable=AsyncMock,
        ) as mocked_validate:
            validate_provider_configuration_task.apply(
                args=[provider.pk, fingerprint],
                task_id="task-123",
            )

        provider.refresh_from_db()
        mocked_validate.assert_not_called()
        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.UNTESTED)
        self.assertEqual(provider.validation_task_id, "")
