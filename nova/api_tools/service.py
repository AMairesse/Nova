from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as jsonschema_validate

from nova.models.APIToolOperation import APIToolOperation
from nova.models.Tool import Tool, ToolCredential
from nova.web.download_service import infer_download_filename

logger = logging.getLogger(__name__)

API_CALL_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class APIServiceError(Exception):
    pass


def _normalize_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_normalize_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_jsonable(item) for key, item in value.items()}
    return str(value)


def _resolve_api_key_parts(credential: ToolCredential) -> tuple[str, str]:
    config = dict(credential.config or {})
    name = str(config.get("api_key_name") or "").strip() or "X-API-Key"
    location = str(config.get("api_key_in") or "").strip().lower() or "header"
    if location not in {"header", "query"}:
        location = "header"
    return name, location


def _build_auth_parts(credential: ToolCredential | None) -> tuple[httpx.Auth | None, dict[str, str], dict[str, str]]:
    if credential is None:
        return None, {}, {}

    auth_type = str(credential.auth_type or "").strip().lower()
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    auth: httpx.Auth | None = None

    if auth_type == "basic":
        username = str(credential.username or "").strip()
        password = str(credential.password or "")
        if username:
            auth = httpx.BasicAuth(username, password)
    elif auth_type in {"token", "oauth"}:
        token = str(credential.token or credential.access_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key":
        token = str(credential.token or "").strip()
        if token:
            name, location = _resolve_api_key_parts(credential)
            if location == "query":
                params[name] = token
            else:
                headers[name] = token

    return auth, headers, params


def _path_placeholders(path_template: str) -> list[str]:
    placeholders: list[str] = []
    current = ""
    inside = False
    for char in str(path_template or ""):
        if char == "{":
            inside = True
            current = ""
            continue
        if char == "}" and inside:
            inside = False
            if current:
                placeholders.append(current)
            current = ""
            continue
        if inside:
            current += char
    return placeholders


def _render_path(path_template: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    remaining = dict(payload or {})
    rendered = str(path_template or "")
    for name in _path_placeholders(path_template):
        if name not in remaining:
            raise APIServiceError(f"Missing required path parameter: {name}")
        value = remaining.pop(name)
        rendered = rendered.replace(f"{{{name}}}", str(value))
    return rendered, remaining


def _validate_input_schema(operation: APIToolOperation, payload: dict[str, Any]) -> None:
    schema = operation.input_schema or {}
    if not schema:
        return
    try:
        jsonschema_validate(instance=payload, schema=schema)
    except JSONSchemaValidationError as exc:
        raise APIServiceError(f"Input validation failed: {exc.message}") from exc


def _normalize_operation_payload(operation: APIToolOperation, payload: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
    _validate_input_schema(operation, payload)
    path, remaining = _render_path(operation.path_template, payload)

    query: dict[str, Any] = {}
    for name in list(operation.query_parameters or []):
        if name in remaining:
            query[name] = remaining.pop(name)

    body: Any = None
    body_parameter = str(operation.body_parameter or "").strip()
    if body_parameter:
        if body_parameter in remaining:
            body = remaining.pop(body_parameter)
    elif remaining:
        body = remaining

    if remaining:
        extras = ", ".join(sorted(remaining.keys()))
        raise APIServiceError(f"Unknown input fields for operation {operation.slug}: {extras}")

    return path, query, body


async def _get_tool_credential(*, tool: Tool, user) -> ToolCredential | None:
    def _load():
        return ToolCredential.objects.filter(user=user, tool=tool).first()

    return await sync_to_async(_load, thread_sensitive=True)()


async def list_api_operations(*, tool: Tool) -> list[dict[str, Any]]:
    def _load():
        return list(
            APIToolOperation.objects.filter(tool=tool, is_active=True).order_by("name", "id")
        )

    operations = await sync_to_async(_load, thread_sensitive=True)()
    return [
        {
            "id": operation.id,
            "name": operation.name,
            "slug": operation.slug,
            "description": operation.description,
            "http_method": operation.http_method,
            "path_template": operation.path_template,
        }
        for operation in operations
    ]


async def describe_api_operation(*, tool: Tool, operation_selector: str) -> dict[str, Any]:
    operation = await resolve_api_operation(tool=tool, operation_selector=operation_selector)
    return {
        "service": {
            "id": tool.id,
            "name": tool.name,
            "endpoint": tool.endpoint,
        },
        "operation": {
            "id": operation.id,
            "name": operation.name,
            "slug": operation.slug,
            "description": operation.description,
            "http_method": operation.http_method,
            "path_template": operation.path_template,
            "query_parameters": list(operation.query_parameters or []),
            "body_parameter": str(operation.body_parameter or "").strip(),
            "input_schema": operation.input_schema or {},
            "output_schema": operation.output_schema or {},
        },
    }


async def resolve_api_operation(*, tool: Tool, operation_selector: str) -> APIToolOperation:
    selector = str(operation_selector or "").strip()
    if not selector:
        raise APIServiceError("API operation selector is required.")

    def _load():
        queryset = APIToolOperation.objects.filter(tool=tool, is_active=True)
        if selector.isdigit():
            match = queryset.filter(id=int(selector)).first()
            if match is not None:
                return match
        return queryset.filter(slug=selector).first() or queryset.filter(name=selector).first()

    operation = await sync_to_async(_load, thread_sensitive=True)()
    if operation is None:
        raise APIServiceError(f"Unknown API operation: {selector}")
    return operation


async def call_api_operation(
    *,
    tool: Tool,
    user,
    operation_selector: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    operation = await resolve_api_operation(tool=tool, operation_selector=operation_selector)
    credential = await _get_tool_credential(tool=tool, user=user)
    auth, auth_headers, auth_params = _build_auth_parts(credential)
    path, query, body = _normalize_operation_payload(operation, payload)

    url = urljoin(str(tool.endpoint or "").rstrip("/") + "/", path.lstrip("/"))
    query_params = {**auth_params, **query}
    headers = dict(auth_headers)
    request_kwargs: dict[str, Any] = {
        "params": query_params,
        "headers": headers,
        "auth": auth,
    }
    if body is not None:
        request_kwargs["json"] = body

    try:
        async with httpx.AsyncClient(timeout=API_CALL_TIMEOUT, follow_redirects=True) as client:
            response = await client.request(operation.http_method, url, **request_kwargs)
            response.raise_for_status()
    except JSONSchemaValidationError as exc:
        raise APIServiceError(f"Input validation failed: {exc.message}") from exc
    except httpx.HTTPStatusError as exc:
        detail = str(exc.response.text or "").strip()
        if detail:
            raise APIServiceError(
                f"API call failed with status {exc.response.status_code}: {detail[:500]}"
            ) from exc
        raise APIServiceError(f"API call failed with status {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        raise APIServiceError(f"API service unreachable: {exc}") from exc

    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    filename = infer_download_filename(str(response.request.url), response.headers)
    body_kind = "binary"
    json_body = None
    text_body = None
    binary_content = bytes(response.content or b"")

    if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
        text_body = response.text
        body_kind = "text"
        if content_type == "application/json":
            try:
                json_body = response.json()
                body_kind = "json"
            except ValueError:
                json_body = None
    else:
        try:
            text_body = response.text
            if text_body and all(ord(char) >= 9 for char in text_body[:200]):
                body_kind = "text"
        except UnicodeDecodeError:
            text_body = None

    payload_envelope = {
        "service": {
            "id": tool.id,
            "name": tool.name,
            "endpoint": tool.endpoint,
        },
        "operation": {
            "id": operation.id,
            "name": operation.name,
            "slug": operation.slug,
            "http_method": operation.http_method,
            "path_template": operation.path_template,
        },
        "request": {
            "url": str(response.request.url),
            "method": operation.http_method,
            "query": _normalize_jsonable(query_params),
            "body": _normalize_jsonable(body),
        },
        "response": {
            "status_code": response.status_code,
            "content_type": content_type or "application/octet-stream",
            "headers": _normalize_jsonable(dict(response.headers)),
            "body_kind": body_kind,
            "json": _normalize_jsonable(json_body),
            "text": text_body if body_kind in {"json", "text"} else None,
            "size": len(binary_content),
            "filename": filename,
        },
    }

    if operation.output_schema and json_body is not None:
        try:
            jsonschema_validate(instance=json_body, schema=operation.output_schema)
        except JSONSchemaValidationError as exc:
            logger.warning(
                "API output validation failed for tool_id=%s operation_id=%s: %s",
                tool.id,
                operation.id,
                exc.message,
            )

    return {
        "payload": payload_envelope,
        "body_kind": body_kind,
        "binary_content": binary_content,
        "filename": filename,
        "content_type": content_type or "application/octet-stream",
    }
