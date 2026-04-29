from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings


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


def _normalized_allowlist() -> tuple[str, ...]:
    values = getattr(settings, "NOVA_EGRESS_ALLOWLIST", []) or []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    return tuple(str(item or "").strip().lower().rstrip(".") for item in values if str(item or "").strip())


def _debug_private_egress_allowed() -> bool:
    return bool(getattr(settings, "DEBUG", False) and getattr(settings, "NOVA_EGRESS_ALLOW_PRIVATE_IN_DEBUG", False))


def _normalize_host_for_policy(hostname: str) -> str:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        raise NetworkPolicyError("URL host is required.")
    return host


def _host_matches_allowlist(hostname: str) -> bool:
    host = _normalize_host_for_policy(hostname)
    for raw_entry in _normalized_allowlist():
        entry = raw_entry
        if "://" in entry:
            entry = (urlsplit(entry).hostname or "").lower().rstrip(".")
        if not entry:
            continue
        if "/" in entry:
            try:
                ipaddress.ip_network(entry, strict=False)
                continue
            except ValueError:
                pass
        if entry.startswith("*."):
            suffix = entry[1:]
            if host.endswith(suffix) and host != entry[2:]:
                return True
        elif "*" in entry:
            if fnmatch.fnmatch(host, entry):
                return True
        elif host == entry:
            return True
    return False


def _ip_matches_allowlist(address: ipaddress._BaseAddress) -> bool:
    for raw_entry in _normalized_allowlist():
        entry = raw_entry
        if "://" in entry:
            entry = (urlsplit(entry).hostname or "").lower().rstrip(".")
        if not entry:
            continue
        try:
            if "/" in entry:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            elif address == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def _is_single_label_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        return "." not in hostname


def _reject_obviously_local_hostname(hostname: str) -> None:
    host = _normalize_host_for_policy(hostname)
    if _host_matches_allowlist(host) or _debug_private_egress_allowed():
        return
    if host in _OBVIOUSLY_LOCAL_HOSTS or any(host.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES):
        raise NetworkPolicyError(
            f"Access to local or private network targets is blocked for agent-provided URLs ({host})."
        )
    if _is_single_label_hostname(host):
        raise NetworkPolicyError(
            f"Access to single-label hostnames is blocked for agent-provided URLs ({host})."
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


def _resolve_allowed_host_port(hostname: str, port: int) -> ResolvedHostPort:
    host = str(hostname or "").strip().rstrip(".")
    lowered_host = _normalize_host_for_policy(host)
    _reject_obviously_local_hostname(lowered_host)
    validated_port = int(port or 0)
    if validated_port <= 0:
        raise NetworkPolicyError("A valid network port is required.")

    host_allowlisted = _host_matches_allowlist(lowered_host)
    debug_private_allowed = _debug_private_egress_allowed()

    try:
        literal_ip = ipaddress.ip_address(lowered_host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        reason = _classify_blocked_ip(literal_ip)
        if reason and not (host_allowlisted or _ip_matches_allowlist(literal_ip) or debug_private_allowed):
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )
        return ResolvedHostPort(hostname=host, ip=str(literal_ip), port=validated_port)

    addresses = _resolve_host_addresses(host, validated_port)
    for address in addresses:
        reason = _classify_blocked_ip(address)
        if reason and not (host_allowlisted or _ip_matches_allowlist(address) or debug_private_allowed):
            raise NetworkPolicyError(
                f"Access to local or private network targets is blocked for agent-provided URLs ({reason})."
            )
    return ResolvedHostPort(hostname=host, ip=str(addresses[0]), port=validated_port)


def assert_allowed_egress_url_sync(url: str, *, schemes: set[str] | tuple[str, ...] = ("http", "https")) -> ResolvedHttpTarget:
    candidate = str(url or "").strip()
    parsed = urlsplit(candidate)
    allowed_schemes = set(schemes)
    if parsed.scheme not in allowed_schemes:
        raise NetworkPolicyError(f"Only {', '.join(sorted(allowed_schemes))} URLs are allowed.")
    if not parsed.hostname:
        raise NetworkPolicyError("URL host is required.")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise NetworkPolicyError("A valid network port is required.") from exc
    port = parsed_port or (443 if parsed.scheme == "https" else 80)
    resolved = _resolve_allowed_host_port(parsed.hostname, port)
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


async def assert_allowed_egress_url(url: str, *, schemes: set[str] | tuple[str, ...] = ("http", "https")) -> ResolvedHttpTarget:
    return await asyncio.to_thread(assert_allowed_egress_url_sync, url, schemes=schemes)


def assert_allowed_egress_host_port_sync(hostname: str, port: int) -> ResolvedHostPort:
    host = str(hostname or "").strip()
    if not host:
        raise NetworkPolicyError("URL host is required.")
    return _resolve_allowed_host_port(host, int(port or 0))


async def assert_allowed_egress_host_port(hostname: str, port: int) -> ResolvedHostPort:
    return await asyncio.to_thread(assert_allowed_egress_host_port_sync, hostname, int(port or 0))


async def assert_public_http_url(url: str) -> ResolvedHttpTarget:
    return await assert_allowed_egress_url(url)


async def assert_public_host_port(hostname: str, port: int) -> ResolvedHostPort:
    host = str(hostname or "").strip()
    if not host:
        raise NetworkPolicyError("URL host is required.")
    return await assert_allowed_egress_host_port(host, int(port or 0))
