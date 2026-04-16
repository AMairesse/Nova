from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

from nova.web.network_policy import assert_public_host_port, assert_public_http_url


@dataclass(slots=True, frozen=True)
class ExecRunnerProxyConfig:
    host: str = "0.0.0.0"
    port: int = 8091


class ExecRunnerProxyServer:
    def __init__(self, config: ExecRunnerProxyConfig):
        self.config = config
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.host,
            port=self.config.port,
        )

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            header_block = await reader.readuntil(b"\r\n\r\n")
            header_text = header_block.decode("latin-1", errors="replace")
            request_line, *header_lines = header_text.split("\r\n")
            method, target, version = request_line.split(" ", 2)

            if method.upper() == "CONNECT":
                upstream_reader, upstream_writer = await self._open_connect_target(target)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(
                    self._relay_stream(reader, upstream_writer),
                    self._relay_stream(upstream_reader, writer),
                )
                return

            upstream_reader, upstream_writer, outbound_request = await self._build_http_request(
                method=method,
                target=target,
                version=version,
                header_lines=header_lines,
            )
            upstream_writer.write(outbound_request)
            await upstream_writer.drain()
            await self._forward_http_request_body(reader, upstream_writer, header_lines)
            await self._relay_stream(upstream_reader, writer)
        except (asyncio.IncompleteReadError, ValueError):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except Exception:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await writer.drain()
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            writer.close()
            await writer.wait_closed()

    async def _open_connect_target(
        self,
        target: str,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        host, _, port_text = str(target or "").rpartition(":")
        port = int(port_text or "443")
        validated_host, validated_port = await assert_public_host_port(host, port)
        return await asyncio.open_connection(validated_host, validated_port)

    async def _build_http_request(
        self,
        *,
        method: str,
        target: str,
        version: str,
        header_lines: list[str],
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
        validated_target = await assert_public_http_url(target)
        parsed = urlsplit(validated_target)
        host = str(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        await assert_public_host_port(host, port)
        upstream_reader, upstream_writer = await asyncio.open_connection(
            host,
            port,
            ssl=parsed.scheme == "https",
            server_hostname=host if parsed.scheme == "https" else None,
        )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        filtered_headers: list[str] = []
        for raw_header in header_lines:
            header = str(raw_header or "").strip()
            if not header:
                continue
            lowered = header.lower()
            if lowered.startswith("proxy-connection:"):
                continue
            filtered_headers.append(header)
        outbound = "\r\n".join([f"{method} {path} {version}", *filtered_headers, "", ""]).encode("latin-1")
        return upstream_reader, upstream_writer, outbound

    @staticmethod
    def _get_header_value(header_lines: list[str], header_name: str) -> str:
        prefix = f"{header_name.lower()}:"
        for raw_header in header_lines:
            header = str(raw_header or "").strip()
            if header.lower().startswith(prefix):
                return header.split(":", 1)[1].strip()
        return ""

    async def _forward_http_request_body(
        self,
        reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        header_lines: list[str],
    ) -> None:
        transfer_encoding = self._get_header_value(header_lines, "transfer-encoding").lower()
        if "chunked" in transfer_encoding:
            await self._forward_chunked_body(reader, upstream_writer)
            return

        content_length_text = self._get_header_value(header_lines, "content-length")
        if not content_length_text:
            return
        content_length = int(content_length_text)
        remaining = max(content_length, 0)
        while remaining > 0:
            chunk = await reader.read(min(65536, remaining))
            if not chunk:
                raise asyncio.IncompleteReadError(partial=b"", expected=remaining)
            upstream_writer.write(chunk)
            await upstream_writer.drain()
            remaining -= len(chunk)

    async def _forward_chunked_body(
        self,
        reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk_header = await reader.readuntil(b"\r\n")
            upstream_writer.write(chunk_header)
            await upstream_writer.drain()

            chunk_size_text = chunk_header.split(b";", 1)[0].strip()
            chunk_size = int(chunk_size_text, 16)
            if chunk_size == 0:
                while True:
                    trailer_line = await reader.readuntil(b"\r\n")
                    upstream_writer.write(trailer_line)
                    await upstream_writer.drain()
                    if trailer_line == b"\r\n":
                        return

            chunk_payload = await reader.readexactly(chunk_size + 2)
            upstream_writer.write(chunk_payload)
            await upstream_writer.drain()

    async def _relay_stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        finally:
            try:
                writer.write_eof()
            except Exception:
                pass
