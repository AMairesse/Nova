# nova/tests/test_security_views.py
import json
from django.test import SimpleTestCase, RequestFactory
from django.conf import settings

from nova.views.security_views import csrf_token as csrf_token_view


class CsrfTokenViewTests(SimpleTestCase):
    def setUp(self):
        """
        Prepare a RequestFactory and resolve the CSRF cookie name so each test
        can exercise the CSRF token endpoint in isolation.
        """
        self.factory = RequestFactory()
        self.cookie_name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken")

    def test_get_returns_token_and_sets_cookie(self):
        """
        Ensure GET /api/security/csrf-token/:
        - returns HTTP 200 with a non-empty csrfToken in JSON
        - sets the CSRF cookie
        - returns a token value consistent with the cookie (using Django's
          _unmask_cipher_token).
        """
        request = self.factory.get("/api/security/csrf-token/")
        response = csrf_token_view(request)

        self.assertEqual(response.status_code, 200)

        # Check JSON
        data = json.loads(response.content.decode("utf-8"))
        self.assertIn("csrfToken", data)
        self.assertTrue(data["csrfToken"])

        # Check cookie
        self.assertIn(self.cookie_name, response.cookies)
        cookie_value = response.cookies[self.cookie_name].value
        self.assertTrue(cookie_value)

        from django.middleware.csrf import _unmask_cipher_token
        unmasked = _unmask_cipher_token(data["csrfToken"])
        self.assertEqual(unmasked, cookie_value)

    def test_post_is_not_allowed(self):
        """
        Verify that POST to the CSRF token endpoint is rejected with 405,
        ensuring the view only supports safe retrieval of tokens via GET.
        """
        request = self.factory.post("/api/security/csrf-token/", data={})
        response = csrf_token_view(request)
        self.assertEqual(response.status_code, 405)
