from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import SimpleTestCase

from nova.web.browser_service import BrowserSession, BrowserSessionError
from nova.web.download_service import DEFAULT_DOWNLOAD_USER_AGENT, download_http_file
from nova.web.network_policy import NetworkPolicyError, assert_public_http_url


class _FakeDownloadStreamResponse:
    def __init__(self, *, url: str, status_code: int, headers: dict[str, str], chunks: list[bytes] | None = None):
        self.status_code = status_code
        self.headers = headers
        self.request = type("Request", (), {"url": url})()
        self._chunks = list(chunks or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakePlaywrightManager:
    def __init__(self, playwright):
        self._playwright = playwright

    async def start(self):
        return self._playwright


class _FailingChromium:
    async def launch(self, **kwargs):
        raise RuntimeError(f"launch failed: {kwargs}")


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FailingChromium()

    async def stop(self):
        return None


class NetworkPolicyTests(SimpleTestCase):
    def test_blocks_obviously_local_hosts(self):
        with self.assertRaises(NetworkPolicyError):
            async_to_sync(assert_public_http_url)("http://localhost/admin")

    def test_blocks_mixed_dns_results_when_one_ip_is_private(self):
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(
                ipaddress.ip_address("93.184.216.34"),
                ipaddress.ip_address("10.0.0.7"),
            ),
        ):
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(assert_public_http_url)("https://example.com/resource")

    def test_allows_public_hosts(self):
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ):
            target = async_to_sync(assert_public_http_url)("https://example.com/resource")

        self.assertEqual(target.url, "https://example.com/resource")
        self.assertEqual(target.hostname, "example.com")
        self.assertEqual(target.ip, "93.184.216.34")

    def test_resolved_target_keeps_original_hostname_while_binding_public_ip(self):
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ):
            target = async_to_sync(assert_public_http_url)("https://Example.COM:8443/resource?q=1")

        self.assertEqual(target.hostname, "example.com")
        self.assertEqual(target.ip, "93.184.216.34")
        self.assertEqual(target.port, 8443)
        self.assertEqual(target.path_with_query, "/resource?q=1")

    def test_download_revalidates_redirect_targets(self):
        requests: list[str] = []

        class FakeAsyncClient:
            def __init__(self, *, headers=None, **kwargs):
                del kwargs
                self.headers = dict(headers or {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                requests.append(f"{method} {url}")
                if len(requests) == 1:
                    return _FakeDownloadStreamResponse(
                        url=url,
                        status_code=302,
                        headers={"location": "http://127.0.0.1/private"},
                    )
                return _FakeDownloadStreamResponse(
                    url=url,
                    status_code=200,
                    headers={"content-type": "text/plain"},
                    chunks=[b"ok"],
                )

        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient):
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(download_http_file)("https://example.com/redirect")

        self.assertEqual(requests, ["GET https://example.com/redirect"])

    def test_download_keeps_default_user_agent_for_public_url(self):
        captured_headers = {}

        class FakeAsyncClient:
            def __init__(self, *, headers=None, **kwargs):
                del kwargs
                captured_headers.update(dict(headers or {}))

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                del method, url
                return _FakeDownloadStreamResponse(
                    url="https://example.com/file.txt",
                    status_code=200,
                    headers={"content-type": "text/plain"},
                    chunks=[b"hello"],
                )

        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient):
            payload = async_to_sync(download_http_file)("https://example.com/file.txt")

        self.assertEqual(payload["content"], b"hello")
        normalized_headers = {str(name).lower(): value for name, value in captured_headers.items()}
        self.assertEqual(normalized_headers["user-agent"], DEFAULT_DOWNLOAD_USER_AGENT)


class BrowserSecurityTests(SimpleTestCase):
    def test_browser_blocks_private_subresources(self):
        route = AsyncMock()
        route.request.url = "http://127.0.0.1/private.js"
        session = BrowserSession()

        async_to_sync(session._handle_route)(route)

        route.abort.assert_awaited_once_with("blockedbyclient")
        self.assertIn("blocked", session._blocked_request_error.lower())

    def test_browser_launch_failure_surfaces_secure_configuration_error(self):
        session = BrowserSession()
        with patch(
            "nova.web.browser_service.async_playwright",
            return_value=_FakePlaywrightManager(_FakePlaywright()),
        ):
            with self.assertRaises(BrowserSessionError) as cm:
                async_to_sync(session._ensure_page)()

        self.assertIn("securely", str(cm.exception).lower())


class DjangoSecuritySettingsTests(SimpleTestCase):
    def test_security_settings_are_enabled_in_non_debug_mode(self):
        self.assertFalse(settings.DEBUG)
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, "SAMEORIGIN")
