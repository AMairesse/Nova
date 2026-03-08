from __future__ import annotations

import re
from unittest.mock import patch
from unittest.mock import AsyncMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Provider import LLMProvider, ProviderType

User = get_user_model()


def _valid_capabilities(*, vision_status="pass") -> dict:
    return {
        "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
        "streaming": {"status": "pass", "message": "ok", "latency_ms": 12},
        "tools": {"status": "pass", "message": "ok", "latency_ms": 15},
        "vision": {"status": vision_status, "message": "ok", "latency_ms": 18},
    }


class ProviderViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="provider-user",
            email="provider@example.com",
            password="pass123",
        )
        self.client.login(username="provider-user", password="pass123")

    def _payload(self, **overrides):
        payload = {
            "name": "Vision Provider",
            "provider_type": ProviderType.OPENAI,
            "model": "gpt-4o-mini",
            "api_key": "dummy-secret",
            "base_url": "",
            "additional_config": "{}",
            "max_context_tokens": "4096",
            "from": "providers",
        }
        payload.update(overrides)
        return payload

    @patch("user_settings.views.provider.validate_provider_configuration_task.apply_async")
    def test_test_provider_action_creates_provider_and_starts_background_validation(self, mocked_apply_async):
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data=self._payload(action="test_provider"),
        )

        provider = LLMProvider.objects.get(user=self.user, name="Vision Provider")
        self.assertRedirects(
            response,
            f"{reverse('user_settings:provider-edit', args=[provider.pk])}?from=providers",
            fetch_redirect_response=False,
        )
        provider.refresh_from_db()
        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.TESTING)
        self.assertTrue(provider.validation_task_id)
        self.assertEqual(
            provider.validation_requested_fingerprint,
            provider.compute_validation_fingerprint(),
        )
        mocked_apply_async.assert_called_once_with(
            args=[provider.pk, provider.validation_requested_fingerprint],
            task_id=provider.validation_task_id,
        )

        edit_response = self.client.get(reverse("user_settings:provider-edit", args=[provider.pk]))
        self.assertContains(edit_response, "Validation status")
        self.assertContains(edit_response, "Validation is running in background.")
        self.assertContains(edit_response, "Testing…")
        self.assertNotContains(edit_response, "Provider validation in progress…")
        self.assertRegex(
            edit_response.content.decode(),
            r'id="test-provider-btn"[^>]*disabled',
        )

    def test_save_without_test_keeps_provider_untested(self):
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data=self._payload(),
        )

        provider = LLMProvider.objects.get(user=self.user, name="Vision Provider")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.UNTESTED)

        list_response = self.client.get(reverse("user_settings:providers"))
        self.assertContains(list_response, "Untested")

    def test_editing_validated_provider_marks_status_stale(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Validated Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )
        provider.apply_validation_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "validation_summary": "Validated successfully.",
                "validation_capabilities": _valid_capabilities(),
            }
        )

        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.pk]),
            data=self._payload(
                name=provider.name,
                model="gpt-4.1-mini",
                api_key="",
            ),
        )

        self.assertEqual(response.status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.model, "gpt-4.1-mini")
        self.assertEqual(provider.validation_status, LLMProvider.ValidationStatus.STALE)

    @patch("user_settings.views.provider.validate_provider_configuration_task.apply_async")
    def test_provider_validation_status_endpoint_returns_testing_state(self, mocked_apply_async):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Validated Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )

        self.client.post(
            reverse("user_settings:provider-edit", args=[provider.pk]),
            data=self._payload(
                name=provider.name,
                action="test_provider",
            ),
        )

        provider.refresh_from_db()
        response = self.client.get(
            reverse("user_settings:provider-validation-status", args=[provider.pk])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["validation_status"], LLMProvider.ValidationStatus.TESTING)
        self.assertEqual(payload["validation_task_id"], provider.validation_task_id)
        self.assertTrue(payload["is_testing"])
        mocked_apply_async.assert_called_once()

    @patch("user_settings.views.provider.resolve_provider_capability_snapshot", new_callable=AsyncMock)
    def test_refresh_capabilities_action_updates_provider_snapshot(self, mocked_resolve_snapshot):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Capability Provider",
            provider_type=ProviderType.OPENROUTER,
            model="google/gemini-2.5-flash",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )
        mocked_resolve_snapshot.return_value = {
            "source": "OpenRouter models API",
            "input_modalities": {"text": "pass", "image": "pass", "pdf": "pass"},
            "output_modalities": {"text": "pass"},
            "operations": {"chat": "pass", "tools": "pass"},
            "limits": {"context_tokens": 128000},
            "model_state": {},
        }

        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.pk]),
            data=self._payload(
                name=provider.name,
                provider_type=ProviderType.OPENROUTER,
                model=provider.model,
                action="refresh_capabilities",
            ),
        )

        self.assertEqual(response.status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.capability_snapshot["input_modalities"]["pdf"], "pass")
        self.assertIsNotNone(provider.capability_refreshed_at)

    def test_save_without_model_creates_connection_only_provider(self):
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data=self._payload(model=""),
        )

        self.assertEqual(response.status_code, 302)
        provider = LLMProvider.objects.get(user=self.user, name="Vision Provider")
        self.assertEqual(provider.model, "")

        list_response = self.client.get(reverse("user_settings:providers"))
        self.assertContains(list_response, "Connection only")

    def test_edit_page_for_catalog_provider_exposes_model_catalog_controls(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Provider",
            provider_type=ProviderType.OPENROUTER,
            model="",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )

        response = self.client.get(reverse("user_settings:provider-edit", args=[provider.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="load-provider-models-btn"')
        self.assertContains(response, 'data-model-catalog-url=')
        self.assertContains(response, "Save connection")
        self.assertRegex(
            response.content.decode(),
            r'id="test-provider-btn"[^>]*disabled',
        )

    @patch("user_settings.views.provider.validate_provider_configuration_task.apply_async")
    def test_test_provider_requires_selected_model(self, mocked_apply_async):
        response = self.client.post(
            reverse("user_settings:provider-add"),
            data=self._payload(model="", action="test_provider"),
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("model", form.errors)
        mocked_apply_async.assert_not_called()

    @patch("user_settings.views.provider.resolve_provider_capability_snapshot", new_callable=AsyncMock)
    def test_refresh_capabilities_requires_selected_model(self, mocked_resolve_snapshot):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Capability Provider",
            provider_type=ProviderType.OPENROUTER,
            model="",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )

        response = self.client.post(
            reverse("user_settings:provider-edit", args=[provider.pk]),
            data=self._payload(
                name=provider.name,
                provider_type=ProviderType.OPENROUTER,
                model="",
                action="refresh_capabilities",
            ),
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("model", form.errors)
        mocked_resolve_snapshot.assert_not_awaited()

    @patch("user_settings.views.provider.list_provider_models", new_callable=AsyncMock)
    def test_provider_model_catalog_endpoint_returns_catalog(self, mocked_list_models):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Provider",
            provider_type=ProviderType.OPENROUTER,
            model="",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )
        mocked_list_models.return_value = [
            {
                "id": "openai/gpt-4.1-mini",
                "label": "GPT-4.1 Mini",
                "description": "Fast",
                "context_length": 128000,
                "suggested_max_context_tokens": 128000,
                "input_modalities": {"text": "pass", "image": "pass"},
                "output_modalities": {"text": "pass"},
                "operations": {"chat": "pass", "tools": "pass"},
                "pricing": {"prompt": "0.10"},
                "state": {},
                "provider_metadata": {},
            }
        ]

        response = self.client.get(
            reverse("user_settings:provider-model-catalog", args=[provider.pk])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider_id"], provider.pk)
        self.assertEqual(payload["selected_model"], "")
        self.assertEqual(payload["models"][0]["id"], "openai/gpt-4.1-mini")
        mocked_list_models.assert_awaited_once()

    def test_provider_model_catalog_endpoint_rejects_manual_provider_types(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="dummy-secret",
            max_context_tokens=4096,
        )

        response = self.client.get(
            reverse("user_settings:provider-model-catalog", args=[provider.pk])
        )

        self.assertEqual(response.status_code, 400)
