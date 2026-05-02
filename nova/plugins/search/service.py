from __future__ import annotations

from typing import Any

import httpx
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool
from nova.web.search_service import SEARCH_TIMEOUT, get_searxng_config
from nova.web.safe_http import safe_http_request


async def test_searxng_access(tool: Tool) -> dict[str, Any]:
    config = await get_searxng_config(tool)

    response = await safe_http_request(
        "GET",
        config["endpoint"],
        timeout=SEARCH_TIMEOUT,
        params={
            "q": "nova",
            "format": "json",
        },
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(_("Invalid response returned by the SearXNG server."))

    results = payload.get("results")
    if results is None:
        raise ValueError(_("The SearXNG server did not return a valid search payload."))

    count = len(list(results or []))
    if count == 0:
        return {
            "status": "success",
            "message": _("Success connecting – search API reachable but no results were returned for the test query."),
        }
    return {
        "status": "success",
        "message": _("Success connecting – %(count)s result(s) returned for the test query.") % {"count": count},
    }
