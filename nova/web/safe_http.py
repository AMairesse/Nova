from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from nova.web.network_policy import assert_allowed_egress_url, max_redirects

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


async def safe_http_request(
    method: str,
    url: str,
    *,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
    max_redirect_count: int | None = None,
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
            await assert_allowed_egress_url(current_url)
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

            current_url = urljoin(str(response.request.url), location)
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
) -> httpx.Response:
    return await safe_http_request(
        request.method,
        str(request.url),
        headers=dict(request.headers),
        content=request.content,
        timeout=timeout,
        follow_redirects=follow_redirects,
    )
