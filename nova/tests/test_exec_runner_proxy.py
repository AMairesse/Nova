from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from nova.exec_runner.proxy import ExecRunnerProxyConfig, ExecRunnerProxyServer


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
