# nova/tests/test_security_views.py
import json
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.conf import settings
from django.http import HttpResponse

from nova.views.security_views import csrf_token as csrf_token_view
from nova.middleware import AdminIPRestrictionMiddleware


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


class AdminIPRestrictionMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = AdminIPRestrictionMiddleware(lambda r: HttpResponse("OK"))

    @override_settings(ALLOWED_ADMIN_IPS=[])
    def test_no_restriction_when_empty(self):
        """Test that no restriction is applied when ALLOWED_ADMIN_IPS is empty."""
        request = self.factory.get("/supernova-admin/")
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        response = self.middleware(request)
        # Should pass through
        self.assertEqual(response.content.decode(), "OK")

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.1'])
    def test_allowed_ip(self):
        """Test access is allowed for IP in ALLOWED_ADMIN_IPS."""
        request = self.factory.get("/supernova-admin/")
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        response = self.middleware(request)
        self.assertEqual(response.content.decode(), "OK")

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.1'])
    def test_denied_ip(self):
        """Test access is denied for IP not in ALLOWED_ADMIN_IPS."""
        request = self.factory.get("/supernova-admin/")
        request.META['REMOTE_ADDR'] = '10.0.0.1'
        response = self.middleware(request)
        self.assertEqual(response.status_code, 403)

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.0/24'])
    def test_allowed_cidr(self):
        """Test access is allowed for IP in CIDR range."""
        request = self.factory.get("/supernova-admin/")
        request.META['REMOTE_ADDR'] = '192.168.1.50'
        response = self.middleware(request)
        self.assertEqual(response.content.decode(), "OK")

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.0/24'])
    def test_denied_cidr(self):
        """Test access is denied for IP outside CIDR range."""
        request = self.factory.get("/supernova-admin/")
        request.META['REMOTE_ADDR'] = '192.168.2.1'
        response = self.middleware(request)
        self.assertEqual(response.status_code, 403)

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.1'])
    def test_x_forwarded_for(self):
        """Test X-Forwarded-For header is used."""
        request = self.factory.get("/supernova-admin/")
        request.META['HTTP_X_FORWARDED_FOR'] = '192.168.1.1, 10.0.0.1'
        request.META['REMOTE_ADDR'] = '10.0.0.1'
        response = self.middleware(request)
        self.assertEqual(response.content.decode(), "OK")

    @override_settings(ALLOWED_ADMIN_IPS=['192.168.1.1'])
    def test_non_admin_path(self):
        """Test middleware does not affect non-admin paths."""
        request = self.factory.get("/some-other-path/")
        request.META['REMOTE_ADDR'] = '10.0.0.1'
        response = self.middleware(request)
        self.assertEqual(response.content.decode(), "OK")
