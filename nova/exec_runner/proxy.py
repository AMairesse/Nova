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
