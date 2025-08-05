# nova/mcp/client.py
from __future__ import annotations
import asyncio, logging, httpx
import aiohttp
import threading
import base64
import logging
from typing import Any, Dict, List, Optional
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from django.http import Http404
from fastmcp.client import Client as FastMCPClient
from fastmcp.client.transports import StreamableHttpTransport, SSETransport
from fastmcp.client.auth import BearerAuth
from nova.models import ToolCredential
from nova.utils import normalize_url
import json  # For key generation

ALLOWED_TYPES = (str, int, float, bool, type(None))

logger = logging.getLogger(__name__)


def _ensure_sync_context() -> None:
    """Raise if called from inside an event-loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return          # OK, we are in a pure-sync caller
    raise RuntimeError(
        "Blocking method called from async context. "
        "Use the async counterpart instead."
    )

class MCPClient:
    """Thin wrapper around FastMCP – cache + auth + async-first API."""
    def __init__(
        self,
        endpoint: str,
        credential: Optional[ToolCredential] = None,
        transport_type: str = "streamable_http",
    ):
        self.endpoint  = normalize_url(endpoint)
        self.credential = credential
        self.transport_type = transport_type
        self.user_id = getattr(credential.user if credential else None, 'id', None)
        self.safe_endpoint = slugify(self.endpoint)[:80]

    # ---------- Auth / transport helpers ---------------------------------
    def _auth_object(self):
        cred = self.credential
        if not cred:
            return None
        if cred.auth_type in {"token", "oauth", "bearer"} and cred.token:
            return BearerAuth(cred.token)
        if cred.auth_type == "none":
            return None
        return BearerAuth(cred.token) if cred.token else None

    def _transport(self) -> StreamableHttpTransport | SSETransport:
        auth = self._auth_object()
        
        if self.transport_type == "sse":
            return SSETransport(url=self.endpoint, auth=auth) if auth else SSETransport(url=self.endpoint)
        # default: streamable_http
        return StreamableHttpTransport(url=self.endpoint, auth=auth) if auth else StreamableHttpTransport(url=self.endpoint)

    # ---------- Async API -------------------------------------------------
    async def alist_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Async list tools with global Django cache."""
        cache_key = f"mcp_tools::{self.safe_endpoint}::{self.user_id or 'anon'}"
        
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        def _get_attr(obj, *attrs, default=None):
            for attr in attrs:
                if hasattr(obj, attr):
                    return getattr(obj, attr)
            return default

        async with FastMCPClient(self._transport()) as client:
            tools = await client.list_tools()
            result = [
                dict(
                    name=t.name,
                    description=getattr(t, "description", ""),
                    input_schema=_get_attr(t, "input_schema", "inputSchema", {}),
                    output_schema=_get_attr(t, "output_schema", "outputSchema", {}),
                )
                for t in tools
            ]
            
            cache.set(cache_key, result, timeout=300)
            return result

    async def acall(self, tool_name: str, **inputs):
        """
        Async call to a MCP tool with global Django cache.

        NOTE: the dictionary `inputs` is passed *as one argument* to
        FastMCPClient.call_tool().
        """
        # Generate a unique cache key that includes inputs + user/tool context
        input_key = json.dumps((tool_name, sorted(inputs.items())), sort_keys=True)
        safe_input_key = base64.urlsafe_b64encode(input_key.encode('utf-8')).decode('utf-8')
        cache_key = f"mcp_call::{self.safe_endpoint}::{self.user_id or 'anon'}::{safe_input_key}"
              
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            async with FastMCPClient(self._transport()) as client:
                result = await client.call_tool(tool_name, inputs)
                cache.set(cache_key, result, timeout=300)  # TTL 5 min
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error calling {tool_name}: {e}")
            if e.response.status_code == 404:
                raise Http404(f"Tool '{tool_name}' not found") from e
            raise
        except httpx.RequestError as e:
            logger.error(f"Connection error calling {tool_name}: {e}")
            raise ConnectionError("MCP server unreachable") from e
        except Exception as e:
            logger.error(f"Error calling {tool_name}: {e}")
            raise

    # ---------- Sync helpers (for Django views / form tests) --------------
    def list_tools(self, user_id: Optional[int] = None, force_refresh: bool = False):
        _ensure_sync_context()
        return asyncio.run(self.alist_tools(force_refresh=force_refresh))

    def call(self, tool_name: str, **inputs):
        _ensure_sync_context()
        self._validate_inputs(inputs)
        return asyncio.run(self.acall(tool_name, **inputs))

    # ---------- misc helpers ---------------------------------------------
    def _validate_inputs(self, inputs: dict[str, Any], depth=0) -> None:
        if depth > 5:  # Prevent infinite recursion
            raise ValidationError("Input nesting too deep")
        for k, v in inputs.items():
            if isinstance(v, (dict, list)):
                self._validate_inputs(v if isinstance(v, dict) else dict(enumerate(v)), depth + 1)
            elif not isinstance(v, ALLOWED_TYPES):
                raise ValidationError(f"Unsupported type for '{k}': {type(v)}")
            if isinstance(v, str) and len(v) > 2048:
                raise ValidationError(f"Value for '{k}' is too long")
