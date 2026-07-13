from django.test import TestCase, override_settings


class OIDCAuthModeTests(TestCase):
    @override_settings(NOVA_AUTH_MODE="local")
    def test_local_login_renders_password_form(self):
        response = self.client.get("/accounts/login/")
        self.assertContains(response, 'name="password"')
        self.assertNotContains(response, "OpenID Connect")

    @override_settings(NOVA_AUTH_MODE="both")
    def test_both_login_renders_both_options(self):
        response = self.client.get("/accounts/login/")
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "OpenID Connect")
        self.assertContains(response, 'method="post"')

    @override_settings(NOVA_AUTH_MODE="oidc_only")
    def test_oidc_only_redirects_and_blocks_password_reset(self):
        response = self.client.get("/accounts/login/?next=/settings/")
        self.assertRedirects(response, "/accounts/oidc/start/?next=%2Fsettings%2F", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/accounts/password_reset/").status_code, 404)
