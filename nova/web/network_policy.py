from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class NetworkPolicyError(ValueError):
    """Raised when an agent-provided URL targets a non-public network."""


@dataclass(slots=True, frozen=True)
class ResolvedHostPort:
    hostname: str
    ip: str
    port: int


@dataclass(slots=True, frozen=True)
class ResolvedHttpTarget:
    url: str
    scheme: str
    hostname: str
    ip: str
    port: int
    path_with_query: str


_OBVIOUSLY_LOCAL_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "docker.internal",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.internal",
}
_LOCAL_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".localdomain",
)
_CLOUD_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_MAX_REDIRECTS = 5


def max_redirects() -> int:
    return _MAX_REDIRECTS


def _classify_blocked_ip(address: ipaddress._BaseAddress) -> str | None:
    if address in _CLOUD_METADATA_IPS:
        return f"cloud metadata IP {address}"
    if address.version == 4 and address in _CGNAT_NETWORK:
        return f"carrier-grade NAT IP {address}"
    if address.is_loopback:
        return f"loopback IP {address}"
    if address.is_private:
        return f"private IP {address}"
    if address.is_link_local:
        return f"link-local IP {address}"
    if address.is_multicast:
        return f"multicast IP {address}"
    if address.is_unspecified:
        return f"unspecified IP {address}"
    if address.is_reserved:
        return f"reserved IP {address}"
    return None


def _reject_obviously_local_hostname(hostname: str) -> None:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        raise NetworkPolicyError("URL host is required.")
    if host in _OBVIOUSLY_LOCAL_HOSTS or any(host.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES):
        raise NetworkPolicyError(
            f"Access to local or private network targets is blocked for agent-provided URLs ({host})."
        )


def _resolve_host_addresses(hostname: str, port: int) -> tuple[ipaddress._BaseAddress, ...]:
    addresses: list[ipaddress._BaseAddress] = []
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkPolicyError(f"Could not resolve host: {hostname}") from exc
    for _family, _type, _proto, _canonname, sockaddr in infos:
        candidate = str(sockaddr[0] or "").strip()
        if not candidate:
            continue
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise NetworkPolicyError(f"Could not resolve host: {hostname}")
    return tuple(addresses)


def _resolve_public_host_port(hostname: str, port: int) -> ResolvedHostPort:
    host = str(hostname or "").strip().rstrip(".")
    lowered_host = host.lower()
    _reject_obviously_local_hostname(lowered_host)
    validated_port = int(port or 0)
    if validated_port <= 0:
        raise NetworkPolicyError("A valid network port is required.")

    try:
        literal_ip = ipaddress.ip_address(lowered_host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        reason = _classify_blocked_ip(literal_ip)
        if reason:
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )
        return ResolvedHostPort(hostname=host, ip=str(literal_ip), port=validated_port)

    addresses = _resolve_host_addresses(host, validated_port)
    for address in addresses:
        reason = _classify_blocked_ip(address)
        if reason:
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )
    return ResolvedHostPort(hostname=host, ip=str(addresses[0]), port=validated_port)


async def assert_public_http_url(url: str) -> ResolvedHttpTarget:
    candidate = str(url or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkPolicyError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise NetworkPolicyError("URL host is required.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolved = await asyncio.to_thread(_resolve_public_host_port, parsed.hostname, port)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return ResolvedHttpTarget(
        url=candidate,
        scheme=parsed.scheme,
        hostname=resolved.hostname,
        ip=resolved.ip,
        port=resolved.port,
        path_with_query=path,
    )


async def assert_public_host_port(hostname: str, port: int) -> ResolvedHostPort:
    host = str(hostname or "").strip()
    if not host:
        raise NetworkPolicyError("URL host is required.")
    return await asyncio.to_thread(_resolve_public_host_port, host, int(port or 0))
