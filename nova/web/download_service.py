from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from nova.file_utils import MAX_FILE_SIZE
from nova.web.network_policy import assert_public_http_url, max_redirects

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?')
DEFAULT_DOWNLOAD_USER_AGENT = "NovaTerminal/1.0 (+https://github.com/AMairesse/Nova)"


def infer_download_filename(url: str, headers: Any, explicit_filename: str = "") -> str:
    provided = str(explicit_filename or "").strip()
    if provided:
        return provided

    content_disposition = str(getattr(headers, "get", lambda *_args, **_kwargs: "")("content-disposition") or "").strip()
    if content_disposition:
        match = _FILENAME_RE.search(content_disposition)
        if match:
            candidate = str(match.group(1) or "").strip().strip('"')
            if candidate:
                return candidate

    path = urlparse(str(url or "")).path
    candidate = path.rsplit("/", 1)[-1] if path else ""
    return candidate or "downloaded-file"


async def download_http_file(
    url: str,
    *,
    filename: str = "",
    headers: dict[str, str] | None = None,
    user_agent: str = "",
    max_size: int = MAX_FILE_SIZE,
) -> dict[str, Any]:
    bytes_read = 0
    chunks: list[bytes] = []
    request_headers = httpx.Headers()
    for name, value in dict(headers or {}).items():
        normalized_name = str(name or "").strip()
        normalized_value = str(value or "").strip()
        if not normalized_name or not normalized_value:
            continue
        request_headers[normalized_name] = normalized_value
    effective_user_agent = str(user_agent or "").strip()
    if effective_user_agent:
        request_headers["User-Agent"] = effective_user_agent
    elif "User-Agent" not in request_headers:
        request_headers["User-Agent"] = DEFAULT_DOWNLOAD_USER_AGENT

    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT,
        follow_redirects=False,
        headers=request_headers,
    ) as client:
        current_url = await assert_public_http_url(url)
        redirect_count = 0

        while True:
            async with client.stream("GET", current_url) as response:
                if 300 <= response.status_code < 400:
                    location = str(response.headers.get("location") or "").strip()
                    if not location:
                        response.raise_for_status()
                    redirect_count += 1
                    if redirect_count > max_redirects():
                        raise ValueError("Too many redirects while downloading the requested URL.")
                    current_url = await assert_public_http_url(urljoin(str(response.request.url), location))
                    continue

                response.raise_for_status()
                inferred_name = infer_download_filename(current_url, response.headers, filename)
                mime_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    bytes_read += len(chunk)
                    if bytes_read > max_size:
                        raise ValueError(f"Downloaded file exceeds the {max_size} byte limit.")
                    chunks.append(chunk)
                break

    return {
        "url": str(current_url or ""),
        "filename": inferred_name,
        "mime_type": mime_type or "application/octet-stream",
        "content": b"".join(chunks),
        "size": bytes_read,
    }
