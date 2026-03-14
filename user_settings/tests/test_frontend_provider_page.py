from __future__ import annotations

import json
import time

from django.contrib.auth import get_user_model
from django.urls import reverse

from nova.models.Provider import ProviderType
from nova.tests.factories import create_provider
from nova.tests.playwright_base import PlaywrightLiveServerTestCase

User = get_user_model()


class ProviderPageFrontendTests(PlaywrightLiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="provider-browser-user",
            email="provider-browser@example.com",
            password="testpass123",
        )
        self.login_to_browser(self.user)

    def _wait_until(self, predicate, *, timeout: float = 4.0, interval: float = 0.05):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(interval)
        self.fail("Condition not met before timeout.")

    def test_add_page_updates_defaults_for_catalog_provider(self):
        self.open_path(reverse("user_settings:provider-add"))
        self.page.wait_for_selector("#provider-form")

        load_models_button = self.page.locator("#load-provider-models-btn")
        self.assertTrue(load_models_button.is_disabled())

        self.page.locator("#id_provider_type").select_option(ProviderType.OPENROUTER)
        self.page.wait_for_function(
            "() => document.querySelector('#id_base_url').value === 'https://openrouter.ai/api/v1'"
        )
        self.page.wait_for_function(
            "() => document.querySelector('#id_max_context_tokens').value === '100000'"
        )

        empty_state_text = self.page.locator("#provider-model-catalog-empty").inner_text()
        self.assertIn("Save this connection first", empty_state_text)

    def test_saved_provider_can_load_catalog_and_select_model(self):
        provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Catalog Provider",
            model="",
        )

        catalog_url = f"{self.live_server_url}{reverse('user_settings:provider-model-catalog', args=[provider.pk])}"
        payload = {
            "provider_id": provider.pk,
            "provider_type": provider.provider_type,
            "selected_model": "",
            "models": [
                {
                    "id": "openai/gpt-4.1-mini",
                    "label": "GPT-4.1 Mini",
                    "description": "Fast general-purpose model.",
                    "context_length": 128000,
                    "suggested_max_context_tokens": 128000,
                    "input_modalities": {"text": "pass", "image": "pass"},
                    "output_modalities": {"text": "pass"},
                    "operations": {"chat": "pass", "tools": "pass"},
                    "pricing": {},
                    "provider_metadata": {"publisher": "OpenAI"},
                    "state": {},
                },
                {
                    "id": "anthropic/claude-3.5-haiku",
                    "label": "Claude 3.5 Haiku",
                    "description": "Compact model.",
                    "context_length": 200000,
                    "suggested_max_context_tokens": 200000,
                    "input_modalities": {"text": "pass"},
                    "output_modalities": {"text": "pass"},
                    "operations": {"chat": "pass"},
                    "pricing": {},
                    "provider_metadata": {"publisher": "Anthropic"},
                    "state": {},
                },
            ],
        }

        self.page.route(
            catalog_url,
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            ),
        )

        self.open_path(reverse("user_settings:provider-edit", args=[provider.pk]))
        self.page.wait_for_selector("#provider-form")

        load_models_button = self.page.locator("#load-provider-models-btn")
        self.assertFalse(load_models_button.is_disabled())
        load_models_button.click()

        self.page.wait_for_selector("[data-provider-model-select='openai/gpt-4.1-mini']")
        status_text = self.page.locator("#provider-model-catalog-status").inner_text()
        self.assertIn("2 model(s) loaded.", status_text)

        self.page.locator("[data-provider-model-select='openai/gpt-4.1-mini']").click()
        self.assertEqual(self.page.locator("#id_model").input_value(), "openai/gpt-4.1-mini")
        self.assertEqual(self.page.locator("#id_max_context_tokens").input_value(), "4096")
        self.assertIn(
            "Manual override. Suggested by model metadata: 128000.",
            self.page.locator("#provider-max-context-note").inner_text(),
        )

        self.page.locator("#provider-reset-max-context-btn").click()
        self.assertEqual(self.page.locator("#id_max_context_tokens").input_value(), "128000")

        summary_text = self.page.locator("#provider-selected-model-summary").inner_text()
        self.assertIn("GPT-4.1 Mini", summary_text)
        self.assertIn("OpenAI", summary_text)

    def test_testing_provider_starts_verification_polling(self):
        provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENAI,
            name="Polling Provider",
            model="gpt-4.1-mini",
        )
        provider.mark_validation_started(task_id="browser-check", requested_fingerprint="browser-check")

        verification_url = (
            f"{self.live_server_url}"
            f"{reverse('user_settings:provider-validation-status', args=[provider.pk])}"
        )
        call_count = {"value": 0}

        def handle_verification_status(route):
            call_count["value"] += 1
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(provider.build_verification_status_payload()),
            )

        self.page.route(verification_url, handle_verification_status)

        self.open_path(reverse("user_settings:provider-edit", args=[provider.pk]))
        self.page.wait_for_selector("#provider-verification-running")
        self.page.wait_for_timeout(2600)

        self._wait_until(lambda: call_count["value"] >= 2)
        self.assertTrue(self.page.locator("#test-provider-btn").is_disabled())
        self.assertTrue(self.page.locator("#refresh-provider-capabilities-btn").is_disabled())
