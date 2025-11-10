# user_settings/tests/test_views.py
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.UserObjects import UserParameters

User = get_user_model()


class GeneralSettingsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="oldpassword123",
        )
        self.client.login(username="testuser", password="oldpassword123")
        self.url = reverse("user_settings:general")
        self.partial_url = f"{self.url}?partial=1"
        self.user_parameters, _ = UserParameters.objects.get_or_create(user=self.user)

    def _post_password_change(
        self,
        *,
        old_password="oldpassword123",
        new_password="newpassword456",
        new_password_confirm=None,
        hx=True,
    ):
        data = {
            "old_password": old_password,
            "new_password1": new_password,
            "new_password2": new_password if new_password_confirm is None else new_password_confirm,
        }
        headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
        return self.client.post(self.partial_url, data, **headers)

    def test_get_general_settings_partial_returns_fragment(self):
        response = self.client.get(self.partial_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/general_form.html")
        self.assertIn("password_form", response.context)
        self.assertContains(response, "Change Password")

    def test_get_general_settings_full_page_includes_sections(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/general_form.html")
        self.assertContains(response, "Langfuse Configuration")
        self.assertContains(response, "API Token Management")

    def test_update_general_settings_form_htmx_refreshes_fragment(self):
        payload = {
            "allow_langfuse": "on",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
            "langfuse_host": "https://langfuse.example.com",
            "api_token_status": "",
        }
        response = self.client.post(
            self.partial_url, payload, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")

        self.user_parameters.refresh_from_db()
        self.assertTrue(self.user_parameters.allow_langfuse)
        self.assertEqual(self.user_parameters.langfuse_public_key, "pk_test")
        self.assertEqual(self.user_parameters.langfuse_secret_key, "sk_test")
        self.assertEqual(
            self.user_parameters.langfuse_host, "https://langfuse.example.com"
        )

    def test_update_general_settings_form_invalid_host_returns_errors(self):
        payload = {
            "allow_langfuse": "",
            "langfuse_public_key": "",
            "langfuse_secret_key": "",
            "langfuse_host": "not-a-valid-url",
            "api_token_status": "",
        }
        response = self.client.post(
            self.partial_url, payload, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid URL", status_code=200)

    def test_password_change_success_htmx(self):
        response = self._post_password_change()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password changed successfully")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword456"))

    def test_password_change_wrong_old_password_htmx(self):
        response = self._post_password_change(old_password="wrongpassword")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change Password")
        self.assertContains(
            response,
            "Your old password was entered incorrectly. Please enter it again.",
        )

    def test_password_change_mismatch_htmx(self):
        response = self._post_password_change(new_password_confirm="differentpassword")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change Password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpassword123"))
