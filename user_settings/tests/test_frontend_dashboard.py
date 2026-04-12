from django.contrib.auth import get_user_model
from django.urls import reverse

from nova.tests.playwright_base import PlaywrightLiveServerTestCase

User = get_user_model()


class SettingsDashboardFrontendTests(PlaywrightLiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="settings-dashboard-user",
            email="settings-dashboard@example.com",
            password="testpass123",
        )
        self.login_to_browser(self.user)

    def test_memory_shortcut_from_tools_opens_memory_tab(self):
        self.open_path(reverse("user_settings:dashboard"))
        self.page.wait_for_selector("#settingsTabs")

        self.page.locator("#tab-tools").click()
        self.page.wait_for_selector('a[href$="#pane-memory"]')

        self.page.get_by_role("link", name="Open memory settings").click()

        self.page.wait_for_selector('#tab-memory[aria-selected="true"]')
        self.page.wait_for_selector("text=Semantic retrieval settings")
        self.page.wait_for_selector("text=Memory records")

    def test_hash_selected_memory_tab_loads_on_initial_dashboard_render(self):
        self.open_path(f"{reverse('user_settings:dashboard')}#pane-memory")

        self.page.wait_for_selector('#tab-memory[aria-selected="true"]')
        self.page.wait_for_selector("text=Semantic retrieval settings")
        self.page.wait_for_selector("text=Memory records")
