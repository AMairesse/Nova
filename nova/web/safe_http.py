from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from nova.web.network_policy import assert_allowed_egress_url, max_redirects

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SENSITIVE_REDIRECT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
}


def _origin_key(url: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(str(url or ""))
    if not parsed.scheme or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return (
        parsed.scheme.lower(),
        parsed.hostname.lower().rstrip("."),
        port or (443 if parsed.scheme.lower() == "https" else 80),
    )


def _is_cross_origin_redirect(previous_url: str, next_url: str) -> bool:
    previous_origin = _origin_key(previous_url)
    next_origin = _origin_key(next_url)
    return bool(previous_origin and next_origin and previous_origin != next_origin)


def _is_sensitive_redirect_header(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return (
        normalized in _SENSITIVE_REDIRECT_HEADERS
        or normalized.endswith("-api-key")
        or normalized.endswith("-token")
    )


def _strip_cross_origin_credentials(request_kwargs: dict[str, Any]) -> None:
    headers = request_kwargs.get("headers")
    if headers is not None:
        sanitized_headers = httpx.Headers(headers)
        for header_name in list(sanitized_headers.keys()):
            if _is_sensitive_redirect_header(header_name):
                del sanitized_headers[header_name]
        request_kwargs["headers"] = sanitized_headers

    request_kwargs.pop("cookies", None)
    request_kwargs.pop("auth", None)


async def safe_http_request(
    method: str,
    url: str,
    *,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
    max_redirect_count: int | None = None,
    allowed_private_hosts: tuple[str, ...] = (),
    **kwargs: Any,
) -> httpx.Response:
    """Issue an outbound HTTP request after validating every egress target."""

    redirect_limit = max_redirects() if max_redirect_count is None else int(max_redirect_count)
    current_method = str(method or "GET").upper()
    current_url = str(url or "").strip()
    request_kwargs = dict(kwargs)
    timeout_value = timeout if timeout is not None else httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(
        timeout=timeout_value,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        redirect_count = 0
        while True:
            await assert_allowed_egress_url(
                current_url,
                allowed_private_hosts=tuple(allowed_private_hosts or ()),
            )
            response = await client.request(current_method, current_url, **request_kwargs)
            if not follow_redirects or response.status_code not in _REDIRECT_STATUSES:
                return response

            location = str(response.headers.get("location") or "").strip()
            if not location:
                return response

            redirect_count += 1
            if redirect_count > redirect_limit:
                raise httpx.TooManyRedirects(
                    "Too many redirects while requesting the configured URL.",
                    request=response.request,
                )

            next_url = urljoin(str(response.request.url), location)
            if _is_cross_origin_redirect(str(response.request.url), next_url):
                _strip_cross_origin_credentials(request_kwargs)
            current_url = next_url
            request_kwargs.pop("params", None)
            if response.status_code == 303 or (
                response.status_code in {301, 302}
                and current_method not in {"GET", "HEAD"}
            ):
                current_method = "GET"
                for body_key in ("content", "data", "files", "json"):
                    request_kwargs.pop(body_key, None)


async def safe_http_send(
    request: httpx.Request,
    *,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
    allowed_private_hosts: tuple[str, ...] = (),
) -> httpx.Response:
    return await safe_http_request(
        request.method,
        str(request.url),
        headers=dict(request.headers),
        content=request.content,
        timeout=timeout,
        follow_redirects=follow_redirects,
        allowed_private_hosts=tuple(allowed_private_hosts or ()),
    )
