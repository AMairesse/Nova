from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool, ToolCredential
from nova.web.safe_http import safe_http_request

SEARXNG_MAX_RESULTS = 10
SEARCH_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _normalize_search_endpoint(host: str) -> str:
    raw = str(host or "").strip().rstrip("/")
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.path.endswith("/search"):
        return raw
    return f"{raw}/search"


async def get_searxng_config(tool: Tool) -> dict[str, Any]:
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    credential = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False,
    )()
    if not credential:
        raise ValueError(_("No credential configured for this SearXNG tool."))

    host = str(credential.config.get("searxng_url") or "").strip()
    if not host:
        raise ValueError(_("Field ‘searxng_url’ is missing from the configuration."))

    try:
        configured_limit = int(credential.config.get("num_results") or 5)
    except (TypeError, ValueError):
        configured_limit = 5

    return {
        "endpoint": _normalize_search_endpoint(host),
        "num_results": max(1, min(configured_limit, SEARXNG_MAX_RESULTS)),
    }


def _normalize_search_results(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(payload.get("results") or [])[:limit]:
        engines = item.get("engines")
        engine_value = item.get("engine")
        if not engine_value and isinstance(engines, list):
            engine_value = ", ".join(str(entry) for entry in engines if str(entry).strip())
        normalized.append(
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "snippet": str(
                    item.get("content")
                    or item.get("snippet")
                    or item.get("description")
                    or ""
                ).strip(),
                "engine": str(engine_value or "").strip(),
                "score": item.get("score"),
            }
        )
    return normalized


async def search_web(tool: Tool, query: str, *, limit: int | None = None) -> dict[str, Any]:
    config = await get_searxng_config(tool)
    effective_limit = max(1, min(int(limit or config["num_results"]), SEARXNG_MAX_RESULTS))

    response = await safe_http_request(
        "GET",
        config["endpoint"],
        timeout=SEARCH_TIMEOUT,
        params={
            "q": str(query or ""),
            "format": "json",
        },
    )
    response.raise_for_status()
    payload = response.json()

    return {
        "query": str(query or ""),
        "results": _normalize_search_results(payload, limit=effective_limit),
        "limit": effective_limit,
    }
