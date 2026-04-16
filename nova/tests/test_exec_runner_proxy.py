from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

from django.test import SimpleTestCase

from nova.exec_runner.proxy import ExecRunnerProxyConfig, ExecRunnerProxyServer
from nova.web.network_policy import ResolvedHostPort, ResolvedHttpTarget


class _MemoryStreamWriter:
    def __init__(self):
        self.buffer = bytearray()
        self.closed = False
        self.wrote_eof = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        self.wrote_eof = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _build_reader(*chunks: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


class ExecRunnerProxyServerTests(SimpleTestCase):
    def test_non_connect_requests_forward_request_body_before_relaying_response(self):
        async def scenario() -> None:
            proxy = ExecRunnerProxyServer(ExecRunnerProxyConfig(host="127.0.0.1", port=8091))
            client_reader = _build_reader(
                b"POST http://example.com/upload HTTP/1.1\r\n"
                b"Host: example.com\r\n"
                b"Content-Length: 11\r\n"
                b"\r\n"
                b"hello world"
            )
            client_writer = _MemoryStreamWriter()
            upstream_reader = _build_reader(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Length: 2\r\n"
                b"\r\n"
                b"ok"
            )
            upstream_writer = _MemoryStreamWriter()

            proxy._build_http_request = AsyncMock(
                return_value=(
                    upstream_reader,
                    upstream_writer,
                    b"POST /upload HTTP/1.1\r\nHost: example.com\r\nContent-Length: 11\r\n\r\n",
                )
            )

            await proxy._handle_client(client_reader, client_writer)

            self.assertEqual(
                bytes(upstream_writer.buffer),
                b"POST /upload HTTP/1.1\r\nHost: example.com\r\nContent-Length: 11\r\n\r\nhello world",
            )
            self.assertIn(b"HTTP/1.1 200 OK", bytes(client_writer.buffer))

        asyncio.run(scenario())

    def test_build_http_request_connects_to_resolved_ip_with_original_sni_and_host_header(self):
        async def scenario() -> None:
            proxy = ExecRunnerProxyServer(ExecRunnerProxyConfig(host="127.0.0.1", port=8091))
            upstream_reader = _build_reader()
            upstream_writer = _MemoryStreamWriter()

            with patch(
                "nova.exec_runner.proxy.assert_public_http_url",
                AsyncMock(
                    return_value=ResolvedHttpTarget(
                        url="https://example.com:8443/path?q=1",
                        scheme="https",
                        hostname="example.com",
                        ip="93.184.216.34",
                        port=8443,
                        path_with_query="/path?q=1",
                    )
                ),
            ), patch(
                "nova.exec_runner.proxy.asyncio.open_connection",
                AsyncMock(return_value=(upstream_reader, upstream_writer)),
            ) as mocked_open:
                _reader, _writer, outbound = await proxy._build_http_request(
                    method="GET",
                    target="https://example.com:8443/path?q=1",
                    version="HTTP/1.1",
                    header_lines=["User-Agent: test", "Host: attacker.test"],
                )

            mocked_open.assert_awaited_once_with(
                "93.184.216.34",
                8443,
                ssl=True,
                server_hostname="example.com",
            )
            rendered = outbound.decode("latin-1")
            self.assertIn("GET /path?q=1 HTTP/1.1", rendered)
            self.assertIn("Host: example.com:8443", rendered)
            self.assertNotIn("Host: attacker.test", rendered)

        asyncio.run(scenario())

    def test_connect_rejects_non_https_ports(self):
        async def scenario() -> None:
            proxy = ExecRunnerProxyServer(ExecRunnerProxyConfig(host="127.0.0.1", port=8091))
            with self.assertRaisesRegex(Exception, "port 443"):
                await proxy._open_connect_target("example.com:22")

        asyncio.run(scenario())

    def test_connect_uses_resolved_ip(self):
        async def scenario() -> None:
            proxy = ExecRunnerProxyServer(ExecRunnerProxyConfig(host="127.0.0.1", port=8091))
            upstream_reader = _build_reader()
            upstream_writer = _MemoryStreamWriter()

            with patch(
                "nova.exec_runner.proxy.assert_public_host_port",
                AsyncMock(return_value=ResolvedHostPort(hostname="example.com", ip="93.184.216.34", port=443)),
            ), patch(
                "nova.exec_runner.proxy.asyncio.open_connection",
                AsyncMock(return_value=(upstream_reader, upstream_writer)),
            ) as mocked_open:
                await proxy._open_connect_target("example.com:443")

            mocked_open.assert_awaited_once_with("93.184.216.34", 443)

        asyncio.run(scenario())
