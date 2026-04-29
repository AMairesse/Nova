from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from nova.web.browser_service import BrowserSession, BrowserSessionError
from nova.web.download_service import DEFAULT_DOWNLOAD_USER_AGENT, download_http_file
from nova.web.network_policy import NetworkPolicyError, assert_allowed_egress_host_port, assert_public_http_url
from nova.web.safe_http import safe_http_request


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


class _CapturingChromium:
    def __init__(self):
        self.launch_calls = []
        self.page = AsyncMock()
        self.context = AsyncMock()
        self.context.route = AsyncMock()
        self.context.new_page = AsyncMock(return_value=self.page)
        self.browser = AsyncMock()
        self.browser.new_context = AsyncMock(return_value=self.context)
        self.browser.close = AsyncMock()
        self.browser.is_connected = lambda: True

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FailingChromium()

    async def stop(self):
        return None


class _CapturingPlaywright:
    def __init__(self):
        self.chromium = _CapturingChromium()

    async def stop(self):
        return None


class NetworkPolicyTests(SimpleTestCase):
    def test_blocks_obviously_local_hosts(self):
        with self.assertRaises(NetworkPolicyError):
            async_to_sync(assert_public_http_url)("http://localhost/admin")

    def test_blocks_private_link_local_metadata_and_single_label_hosts(self):
        blocked_urls = [
            "http://10.0.0.1/admin",
            "http://192.168.1.10/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.100.100.200/latest/meta-data/",
            "http://service:8080/",
            "http://example.local/",
            "http://example.internal/",
        ]
        for url in blocked_urls:
            with self.subTest(url=url):
                with self.assertRaises(NetworkPolicyError):
                    async_to_sync(assert_public_http_url)(url)

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

    @override_settings(NOVA_EGRESS_ALLOWLIST=["10.0.0.0/8", "*.internal"])
    def test_admin_allowlist_allows_private_cidr_and_internal_hostname(self):
        literal = async_to_sync(assert_public_http_url)("http://10.2.3.4/status")
        self.assertEqual(literal.ip, "10.2.3.4")

        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("10.0.0.7"),),
        ):
            target = async_to_sync(assert_public_http_url)("https://api.internal/status")

        self.assertEqual(target.hostname, "api.internal")
        self.assertEqual(target.ip, "10.0.0.7")

    def test_host_port_policy_blocks_private_mail_targets(self):
        with self.assertRaises(NetworkPolicyError):
            async_to_sync(assert_allowed_egress_host_port)("127.0.0.1", 993)

    def test_imap_client_blocks_private_host_before_connect(self):
        from nova.plugins.mail.service import build_imap_client

        credential = SimpleNamespace(
            config={
                "imap_server": "127.0.0.1",
                "imap_port": 993,
                "username": "user",
                "password": "secret",
                "use_ssl": True,
            }
        )
        with patch("nova.plugins.mail.service.imapclient.IMAPClient") as mocked_client:
            with self.assertRaises(NetworkPolicyError):
                build_imap_client(credential)

        mocked_client.assert_not_called()

    def test_webdav_request_blocks_private_url_before_session(self):
        from nova.webdav.service import webdav_request

        config = {
            "server_url": "http://127.0.0.1:8080/dav",
            "root_path": "/",
            "username": "user",
            "password": "secret",
            "timeout": 5,
        }
        with patch("nova.webdav.service.aiohttp.ClientSession") as mocked_session:
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(webdav_request)(config, "PROPFIND", "/")

        mocked_session.assert_not_called()

    def test_custom_embeddings_provider_blocks_private_url(self):
        from nova.llm.embeddings import EmbeddingsProvider, compute_embedding

        provider = EmbeddingsProvider(
            provider_type="custom",
            base_url="http://127.0.0.1:11434/v1",
            model="embed-local",
            api_key="",
        )

        with self.assertRaises(NetworkPolicyError):
            async_to_sync(compute_embedding)("hello", provider_override=provider)

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
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient), patch(
            "nova.web.download_service.SafeHttpProxyServer",
            return_value=AsyncMock(proxy_url="http://127.0.0.1:43123"),
        ):
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(download_http_file)("https://example.com/redirect")

        self.assertEqual(requests, ["GET https://example.com/redirect"])

    def test_safe_http_request_revalidates_redirect_targets(self):
        requests: list[str] = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                del kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def request(self, method, url, **kwargs):
                del kwargs
                requests.append(f"{method} {url}")
                return httpx.Response(
                    302,
                    headers={"location": "http://127.0.0.1/private"},
                    request=httpx.Request(method, url),
                )

        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.safe_http.httpx.AsyncClient", new=FakeAsyncClient):
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(safe_http_request)("GET", "https://example.com/redirect")

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
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient), patch(
            "nova.web.download_service.SafeHttpProxyServer",
            return_value=AsyncMock(proxy_url="http://127.0.0.1:43123"),
        ):
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

    def test_browser_uses_local_safe_proxy_for_navigation(self):
        session = BrowserSession()
        playwright = _CapturingPlaywright()
        fake_proxy = AsyncMock()
        fake_proxy.proxy_url = "http://127.0.0.1:43123"

        with patch(
            "nova.web.browser_service.async_playwright",
            return_value=_FakePlaywrightManager(playwright),
        ), patch(
            "nova.web.browser_service.SafeHttpProxyServer",
            return_value=fake_proxy,
        ):
            async_to_sync(session._ensure_page)()
            async_to_sync(session.close)()

        fake_proxy.start.assert_awaited_once()
        fake_proxy.close.assert_awaited_once()
        self.assertEqual(
            playwright.chromium.launch_calls[0]["proxy"],
            {"server": "http://127.0.0.1:43123"},
        )


class DjangoSecuritySettingsTests(SimpleTestCase):
    def test_security_settings_are_enabled_in_non_debug_mode(self):
        self.assertFalse(settings.DEBUG)
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, "SAMEORIGIN")
