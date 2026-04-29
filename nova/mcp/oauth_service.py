from __future__ import annotations

import base64
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone
from mcp.client.auth.oauth2 import (
    PKCEParameters,
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    handle_token_response_scopes,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata

from nova.models.Tool import Tool, ToolCredential
from nova.utils import normalize_url
from nova.web.safe_http import safe_http_request, safe_http_send

logger = logging.getLogger(__name__)

OAUTH_FLOW_TTL_SECONDS = 600
ACCESS_TOKEN_EXPIRY_SKEW_SECONDS = 30
MCP_OAUTH_CONFIG_KEY = "mcp_oauth"


class MCPOAuthError(Exception):
    pass


class MCPOAuthConnectionRequired(MCPOAuthError):
    pass


class MCPReconnectRequired(MCPOAuthError):
    pass


@dataclass(slots=True, frozen=True)
class MCPOAuthFlowStart:
    authorization_url: str
    state: str


def _oauth_config(credential: ToolCredential) -> dict[str, Any]:
    config = credential.config or {}
    value = config.get(MCP_OAUTH_CONFIG_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _set_oauth_config(credential: ToolCredential, oauth_config: dict[str, Any]) -> None:
    config = dict(credential.config or {})
    config[MCP_OAUTH_CONFIG_KEY] = oauth_config
    credential.config = config


def _auth_server_base_url(auth_server_url: str | None, endpoint: str) -> str:
    if auth_server_url:
        return str(auth_server_url).rstrip("/")
    return normalize_url(endpoint).rstrip("/")


def _choose_token_auth_method(*, client_secret: str | None, explicit_method: str | None) -> str:
    if explicit_method in {"none", "client_secret_post", "client_secret_basic"}:
        return explicit_method
    return "client_secret_post" if client_secret else "none"


def _build_client_info_from_credential(
    *,
    credential: ToolCredential,
    redirect_uri: str,
    scope: str | None,
    token_endpoint_auth_method: str | None,
) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=credential.client_id,
        client_secret=credential.client_secret,
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
        client_name="Nova MCP Client",
        token_endpoint_auth_method=_choose_token_auth_method(
            client_secret=credential.client_secret,
            explicit_method=token_endpoint_auth_method,
        ),
    )


def _prepare_token_auth(
    *,
    client_info: OAuthClientInformationFull,
    data: dict[str, str],
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    headers = dict(headers or {})
    auth_method = client_info.token_endpoint_auth_method or "none"
    if auth_method == "client_secret_basic" and client_info.client_id and client_info.client_secret:
        encoded_id = quote(client_info.client_id, safe="")
        encoded_secret = quote(client_info.client_secret, safe="")
        credentials = f"{encoded_id}:{encoded_secret}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    elif auth_method == "client_secret_post" and client_info.client_secret:
        data["client_secret"] = client_info.client_secret
    return data, headers


def _flow_cache_key(state: str) -> str:
    return f"mcp_oauth_flow::{state}"


async def _save_credential(credential: ToolCredential) -> None:
    await sync_to_async(credential.save, thread_sensitive=True)()


async def _refresh_credential(credential: ToolCredential) -> ToolCredential:
    await sync_to_async(credential.refresh_from_db, thread_sensitive=True)()
    return credential


async def _load_credential_for_user(*, credential_id: int, tool_id: int, user) -> ToolCredential:
    def _load() -> ToolCredential:
        return ToolCredential.objects.select_related("tool").get(
            pk=credential_id,
            tool_id=tool_id,
            user=user,
        )

    return await sync_to_async(_load, thread_sensitive=True)()


def _has_valid_access_token(credential: ToolCredential) -> bool:
    token = str(credential.access_token or "").strip()
    if not token:
        return False
    if credential.expires_at is None:
        return True
    return credential.expires_at > timezone.now() + timedelta(seconds=ACCESS_TOKEN_EXPIRY_SKEW_SECONDS)


def _persist_token_response(
    credential: ToolCredential,
    *,
    token_response,
    oauth_config: dict[str, Any],
) -> None:
    credential.access_token = token_response.access_token
    credential.token_type = token_response.token_type
    if token_response.refresh_token:
        credential.refresh_token = token_response.refresh_token
    if token_response.expires_in:
        credential.expires_at = timezone.now() + timedelta(seconds=int(token_response.expires_in))
    else:
        credential.expires_at = None
    oauth_config["status"] = "connected"
    oauth_config["last_error"] = ""
    if token_response.scope:
        oauth_config["scope"] = token_response.scope
    _set_oauth_config(credential, oauth_config)


async def _mark_reconnect_required(
    credential: ToolCredential,
    *,
    message: str,
    clear_dynamic_client: bool = False,
) -> None:
    oauth_config = _oauth_config(credential)
    oauth_config["status"] = "reconnect_required"
    oauth_config["last_error"] = message
    credential.access_token = None
    credential.refresh_token = None
    credential.expires_at = None
    if clear_dynamic_client or oauth_config.get("client_registration_mode") == "dynamic":
        if oauth_config.get("client_registration_mode") == "dynamic":
            credential.client_id = None
            credential.client_secret = None
    _set_oauth_config(credential, oauth_config)
    await _save_credential(credential)


async def _discover_oauth_metadata(*, endpoint: str) -> tuple[dict[str, Any], OAuthMetadata, str | None]:
    normalized_endpoint = normalize_url(endpoint)
    timeout = httpx.Timeout(10.0, read=10.0)
    probe_response = await safe_http_request("GET", normalized_endpoint, timeout=timeout)
    if probe_response.status_code not in (401, 403):
        probe_response = await safe_http_request("POST", normalized_endpoint, json={}, timeout=timeout)
    if probe_response.status_code not in (401, 403):
        raise MCPOAuthError(
            "The MCP server did not request OAuth authentication during the connection test."
        )

    resource_metadata_hint = extract_resource_metadata_from_www_auth(probe_response)
    selected_resource_metadata_url: str | None = None
    protected_resource_metadata = None
    for url in build_protected_resource_metadata_discovery_urls(resource_metadata_hint, normalized_endpoint):
        response = await safe_http_request("GET", url, timeout=timeout)
        protected_resource_metadata = await handle_protected_resource_response(response)
        if protected_resource_metadata is not None:
            selected_resource_metadata_url = url
            break
    if protected_resource_metadata is None:
        raise MCPOAuthError("Could not discover protected resource metadata for this MCP server.")

    auth_server_url = str(protected_resource_metadata.authorization_servers[0])
    oauth_metadata = None
    for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, normalized_endpoint):
        response = await safe_http_request("GET", url, timeout=timeout)
        ok, oauth_metadata = await handle_auth_metadata_response(response)
        if not ok:
            break
        if oauth_metadata is not None:
            break
    if oauth_metadata is None:
        raise MCPOAuthError("Could not discover the OAuth authorization server metadata.")

    scope = get_client_metadata_scopes(
        extract_scope_from_www_auth(probe_response),
        protected_resource_metadata,
        oauth_metadata,
    )
    return (
        {
            "resource_metadata_url": selected_resource_metadata_url,
            "auth_server_url": auth_server_url,
            "authorization_endpoint": str(oauth_metadata.authorization_endpoint or ""),
            "token_endpoint": str(oauth_metadata.token_endpoint or ""),
            "registration_endpoint": str(oauth_metadata.registration_endpoint or ""),
        },
        oauth_metadata,
        scope,
    )


async def _ensure_client_registration(
    *,
    credential: ToolCredential,
    endpoint: str,
    oauth_metadata: OAuthMetadata,
    redirect_uri: str,
    scope: str | None,
) -> tuple[OAuthClientInformationFull, str]:
    if credential.client_id:
        oauth_config = _oauth_config(credential)
        client_info = _build_client_info_from_credential(
            credential=credential,
            redirect_uri=redirect_uri,
            scope=scope,
            token_endpoint_auth_method=oauth_config.get("token_endpoint_auth_method"),
        )
        mode = "dynamic" if oauth_config.get("client_registration_mode") == "dynamic" else "static"
        return client_info, mode

    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
        client_name="Nova MCP Client",
    )
    registration_request = create_client_registration_request(
        oauth_metadata,
        metadata,
        _auth_server_base_url(
            _oauth_config(credential).get("auth_server_url"),
            endpoint,
        ),
    )
    timeout = httpx.Timeout(10.0, read=10.0)
    response = await safe_http_send(registration_request, timeout=timeout)
    client_info = await handle_registration_response(response)
    credential.client_id = client_info.client_id
    credential.client_secret = client_info.client_secret
    oauth_config = _oauth_config(credential)
    oauth_config["client_registration_mode"] = "dynamic"
    oauth_config["token_endpoint_auth_method"] = client_info.token_endpoint_auth_method or "none"
    oauth_config["client_secret_expires_at"] = client_info.client_secret_expires_at
    _set_oauth_config(credential, oauth_config)
    await _save_credential(credential)
    return client_info, "dynamic"


async def start_mcp_oauth_flow(
    *,
    tool: Tool,
    credential: ToolCredential,
    user,
    redirect_uri: str,
) -> MCPOAuthFlowStart:
    metadata_payload, oauth_metadata, scope = await _discover_oauth_metadata(endpoint=tool.endpoint)
    oauth_config = _oauth_config(credential)
    oauth_config.update(metadata_payload)
    oauth_config["scope"] = scope
    oauth_config["last_error"] = ""
    oauth_config.setdefault("status", "never_connected")
    if credential.client_id and oauth_config.get("client_registration_mode") != "dynamic":
        oauth_config["client_registration_mode"] = "static"
        oauth_config["token_endpoint_auth_method"] = _choose_token_auth_method(
            client_secret=credential.client_secret,
            explicit_method=oauth_config.get("token_endpoint_auth_method"),
        )
    _set_oauth_config(credential, oauth_config)
    await _save_credential(credential)

    client_info, registration_mode = await _ensure_client_registration(
        credential=credential,
        endpoint=tool.endpoint,
        oauth_metadata=oauth_metadata,
        redirect_uri=redirect_uri,
        scope=scope,
    )
    oauth_config = _oauth_config(credential)
    oauth_config["client_registration_mode"] = registration_mode
    oauth_config["token_endpoint_auth_method"] = client_info.token_endpoint_auth_method or "none"
    _set_oauth_config(credential, oauth_config)
    await _save_credential(credential)

    if not oauth_metadata.authorization_endpoint:
        raise MCPOAuthError("The OAuth authorization endpoint is missing from the server metadata.")

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_info.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    authorization_url = f"{oauth_metadata.authorization_endpoint}?{urlencode(params)}"

    cache.set(
        _flow_cache_key(state),
        {
            "user_id": getattr(user, "id", None),
            "tool_id": tool.id,
            "credential_id": credential.id,
            "code_verifier": pkce.code_verifier,
            "redirect_uri": redirect_uri,
        },
        timeout=OAUTH_FLOW_TTL_SECONDS,
    )
    return MCPOAuthFlowStart(authorization_url=authorization_url, state=state)


async def complete_mcp_oauth_flow(
    *,
    user,
    state: str,
    code: str,
) -> tuple[Tool, ToolCredential]:
    flow_state = cache.get(_flow_cache_key(state))
    if not isinstance(flow_state, dict):
        raise MCPOAuthError("The OAuth flow has expired or is invalid.")
    if flow_state.get("user_id") != getattr(user, "id", None):
        raise MCPOAuthError("This OAuth callback does not belong to the current user.")

    credential = await _load_credential_for_user(
        credential_id=int(flow_state["credential_id"]),
        tool_id=int(flow_state["tool_id"]),
        user=user,
    )
    tool = credential.tool
    oauth_config = _oauth_config(credential)
    token_endpoint = str(oauth_config.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise MCPOAuthError("The OAuth token endpoint is missing from the stored MCP configuration.")

    client_info = _build_client_info_from_credential(
        credential=credential,
        redirect_uri=str(flow_state["redirect_uri"]),
        scope=oauth_config.get("scope"),
        token_endpoint_auth_method=oauth_config.get("token_endpoint_auth_method"),
    )
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(flow_state["redirect_uri"]),
        "code_verifier": str(flow_state["code_verifier"]),
        "client_id": str(client_info.client_id or ""),
    }
    data, headers = _prepare_token_auth(client_info=client_info, data=data, headers=headers)
    timeout = httpx.Timeout(10.0, read=10.0)
    response = await safe_http_request(
        "POST",
        token_endpoint,
        data=data,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code != 200:
        body = await response.aread()
        detail = body.decode("utf-8", errors="replace")
        raise MCPOAuthError(f"OAuth token exchange failed ({response.status_code}): {detail}")

    token_response = await handle_token_response_scopes(response)
    _persist_token_response(credential, token_response=token_response, oauth_config=oauth_config)
    await _save_credential(credential)
    cache.delete(_flow_cache_key(state))
    return tool, credential


async def get_valid_mcp_access_token(
    *,
    tool: Tool,
    credential: ToolCredential | None,
    user=None,
) -> str | None:
    del user  # Reserved for future policy checks.
    if credential is None or credential.auth_type != "oauth_managed":
        return None
    credential = await _refresh_credential(credential)
    if _has_valid_access_token(credential):
        return str(credential.access_token or "")

    if not credential.refresh_token:
        oauth_config = _oauth_config(credential)
        message = (
            f'MCP OAuth connection required for "{tool.name}". '
            "Reconnect it in Settings > Tools."
        )
        if oauth_config.get("status") == "reconnect_required":
            raise MCPReconnectRequired(message)
        raise MCPOAuthConnectionRequired(message)

    oauth_config = _oauth_config(credential)
    token_endpoint = str(oauth_config.get("token_endpoint") or "").strip()
    if not token_endpoint:
        await _mark_reconnect_required(
            credential,
            message=(
                f'MCP OAuth reconnect required for "{tool.name}" because the token endpoint is missing. '
                "Reconnect it in Settings > Tools."
            ),
            clear_dynamic_client=False,
        )
        raise MCPReconnectRequired(
            f'MCP OAuth reconnect required for "{tool.name}". Reconnect it in Settings > Tools.'
        )

    client_info = _build_client_info_from_credential(
        credential=credential,
        redirect_uri="http://localhost/unused",
        scope=oauth_config.get("scope"),
        token_endpoint_auth_method=oauth_config.get("token_endpoint_auth_method"),
    )
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": str(credential.refresh_token or ""),
        "client_id": str(client_info.client_id or ""),
    }
    data, headers = _prepare_token_auth(client_info=client_info, data=data, headers=headers)
    timeout = httpx.Timeout(10.0, read=10.0)
    try:
        response = await safe_http_request(
            "POST",
            token_endpoint,
            data=data,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            body = await response.aread()
            detail = body.decode("utf-8", errors="replace")
            clear_dynamic = "invalid_client" in detail.lower()
            await _mark_reconnect_required(
                credential,
                message=(
                    f'MCP OAuth reconnect required for "{tool.name}" after token refresh failed: {detail}'
                ),
                clear_dynamic_client=clear_dynamic,
            )
            raise MCPReconnectRequired(
                f'MCP OAuth reconnect required for "{tool.name}". Reconnect it in Settings > Tools.'
            )
        token_response = await handle_token_response_scopes(response)
        _persist_token_response(credential, token_response=token_response, oauth_config=oauth_config)
        await _save_credential(credential)
        return str(credential.access_token or "")
    except MCPReconnectRequired:
        raise
    except Exception as exc:
        logger.warning("MCP OAuth refresh failed for tool_id=%s: %s", tool.id, exc)
        await _mark_reconnect_required(
            credential,
            message=(
                f'MCP OAuth reconnect required for "{tool.name}" after token refresh failed.'
            ),
            clear_dynamic_client=False,
        )
        raise MCPReconnectRequired(
            f'MCP OAuth reconnect required for "{tool.name}". Reconnect it in Settings > Tools.'
        ) from exc
