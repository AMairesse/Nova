from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from nova.file_utils import MAX_FILE_SIZE

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?')


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
    max_size: int = MAX_FILE_SIZE,
) -> dict[str, Any]:
    bytes_read = 0
    chunks: list[bytes] = []

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            inferred_name = infer_download_filename(url, response.headers, filename)
            mime_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read > max_size:
                    raise ValueError(f"Downloaded file exceeds the {max_size} byte limit.")
                chunks.append(chunk)

    return {
        "url": str(url or ""),
        "filename": inferred_name,
        "mime_type": mime_type or "application/octet-stream",
        "content": b"".join(chunks),
        "size": bytes_read,
    }

