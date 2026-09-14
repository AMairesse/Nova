from django.contrib.auth import get_user_model
from django.urls import reverse

from nova.tests.factories import create_provider
from nova.tests.playwright_base import PlaywrightLiveServerTestCase

User = get_user_model()


class SettingsOnboardingFrontendTests(PlaywrightLiveServerTestCase):
    def test_optional_onboarding_is_resumable_and_lists_safe_provider_details(self):
        user = User.objects.create_user(
            username="onboarding-user",
            email="onboarding@example.com",
            password="testpass123",
        )
        create_provider(user, name="Visible provider", model="safe-model")
        self.login_to_browser(user)

        self.open_path(reverse("user_settings:dashboard"))
        self.page.wait_for_selector("#nova-onboarding")
        self.page.wait_for_selector("text=Visible provider")
        self.page.locator("[data-onboarding-toggle]").click()
        self.page.wait_for_selector("#nova-onboarding .card-body.d-none")
        self.page.locator("[data-onboarding-toggle]").click()
        self.page.wait_for_selector("#nova-onboarding .card-body:not(.d-none)")

    def test_onboarding_first_request_uses_existing_index_and_optional_agent(self):
        user = User.objects.create_user(
            username="onboarding-links-user",
            email="onboarding-links@example.com",
            password="testpass123",
        )
        self.login_to_browser(user)
        self.open_path(reverse("user_settings:dashboard"))
        self.page.wait_for_selector("#nova-onboarding")
        self.assertTrue(
            self.page.locator('[data-onboarding-step="request"]').get_attribute("href").startswith("/")
        )
