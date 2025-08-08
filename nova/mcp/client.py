# nova/mcp/client.py
from __future__ import annotations
import asyncio, logging, httpx
import base64
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
import json

ALLOWED_TYPES = (str, int, float, bool, type(None))

logger = logging.getLogger(__name__)


class MCPClient:
    """Thin wrapper around FastMCP – cache + auth + async-first API."""
    def __init__(
        self,
        endpoint: str,
        thread_id: Optional[int] = None,  # Optionnel (pour tests non-thread ; -1 pour fictif)
        credential: Optional[ToolCredential] = None,
        transport_type: str = "streamable_http",
        user_id: Optional[int] = None,  # Provided by caller for cache keys
    ):
        self.thread_id = thread_id or -1  # -1 pour non-thread (ex: tests)
        self.endpoint = normalize_url(endpoint)
        self.credential = credential
        self.transport_type = transport_type
        self.user_id = user_id
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
        headers = {}
        
        if self.transport_type == "sse":
            return SSETransport(url=self.endpoint, auth=auth, headers=headers) if auth else SSETransport(url=self.endpoint, headers=headers)
        return StreamableHttpTransport(url=self.endpoint, auth=auth, headers=headers) if auth else StreamableHttpTransport(url=self.endpoint, headers=headers)

    # ---------- Async API -------------------------------------------------
    async def alist_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
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

        async with FastMCPClient(self._transport()) as client:  # Per-appel
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
        """
        input_key = json.dumps((tool_name, sorted(inputs.items())), sort_keys=True)
        safe_input_key = base64.urlsafe_b64encode(input_key.encode('utf-8')).decode('utf-8')
        cache_key = f"mcp_call::{self.safe_endpoint}::{self.user_id or 'anon'}::{safe_input_key}"
              
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            transport = self._transport()
            async with FastMCPClient(transport) as client:
                result = await client.call_tool(tool_name, inputs)
                cache.set(cache_key, result, timeout=300)
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

    # ---------- Sync helpers (legacy) --------------
    def list_tools(self, force_refresh: bool = False):
        return asyncio.run(self.alist_tools(force_refresh=force_refresh))

    def call(self, tool_name: str, **inputs):
        self._validate_inputs(inputs)
        return asyncio.run(self.acall(tool_name, **inputs))

    # ---------- misc helpers ---------------------------------------------
    def _validate_inputs(self, inputs: dict[str, Any], depth=0) -> None:
        if depth > 5:
            raise ValidationError("Input nesting too deep")
        for k, v in inputs.items():
            if isinstance(v, (dict, list)):
                self._validate_inputs(v if isinstance(v, dict) else dict(enumerate(v)), depth + 1)
            elif not isinstance(v, ALLOWED_TYPES):
                raise ValidationError(f"Unsupported type for '{k}': {type(v)}")
            if isinstance(v, str) and len(v) > 2048:
                raise ValidationError(f"Value for '{k}' is too long")

