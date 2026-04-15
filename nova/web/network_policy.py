from __future__ import annotations

import asyncio
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlsplit


class NetworkPolicyError(ValueError):
    """Raised when an agent-provided URL targets a non-public network."""


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


@lru_cache(maxsize=512)
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


def _assert_public_host(hostname: str, port: int) -> None:
    host = str(hostname or "").strip().lower().rstrip(".")
    _reject_obviously_local_hostname(host)

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        reason = _classify_blocked_ip(literal_ip)
        if reason:
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )
        return

    for address in _resolve_host_addresses(host, port):
        reason = _classify_blocked_ip(address)
        if reason:
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )


async def assert_public_http_url(url: str) -> str:
    candidate = str(url or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkPolicyError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise NetworkPolicyError("URL host is required.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    await asyncio.to_thread(_assert_public_host, parsed.hostname, port)
    return candidate


async def assert_public_host_port(hostname: str, port: int) -> tuple[str, int]:
    host = str(hostname or "").strip()
    if not host:
        raise NetworkPolicyError("URL host is required.")
    validated_port = int(port or 0)
    if validated_port <= 0:
        raise NetworkPolicyError("A valid network port is required.")
    await asyncio.to_thread(_assert_public_host, host, validated_port)
    return host, validated_port
