from dataclasses import dataclass

from nova.web.network_policy import ResolvedHostPort, ResolvedHttpTarget, assert_public_host_port, assert_public_http_url
from nova.web.safe_proxy import ALLOWED_CONNECT_PORTS, SafeHttpProxyServer


@dataclass(slots=True, frozen=True)
class ExecRunnerProxyConfig:
    host: str = "0.0.0.0"
    port: int = 8091


ExecRunnerProxyServer = SafeHttpProxyServer

__all__ = [
    "ALLOWED_CONNECT_PORTS",
    "ExecRunnerProxyConfig",
    "ExecRunnerProxyServer",
    "ResolvedHostPort",
    "ResolvedHttpTarget",
    "assert_public_host_port",
    "assert_public_http_url",
]
