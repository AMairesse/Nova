from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED_VALUE = "[redacted]"
_SENSITIVE_KEY_TOKENS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "session",
    "set_cookie",
    "token",
)
SAFE_RESPONSE_HEADER_ALLOWLIST = {
    "cache-control",
    "content-disposition",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "location",
}


def _normalize_key_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def is_sensitive_key(name: str, *, extra_sensitive_keys: Iterable[str] | None = None) -> bool:
    normalized = _normalize_key_name(name)
    if not normalized:
        return False
    if any(normalized == _normalize_key_name(item) for item in list(extra_sensitive_keys or [])):
        return True
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def collect_secret_values(*values: object) -> list[str]:
    collected: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, Mapping):
            collected.extend(collect_secret_values(*value.values()))
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            collected.extend(collect_secret_values(*value))
            continue
        text = str(value or "").strip()
        if len(text) < 3:
            continue
        collected.append(text)
    return sorted(dict.fromkeys(collected), key=len, reverse=True)


def redact_known_secret_values(text: str, secret_values: Iterable[str] | None = None) -> str:
    redacted = str(text or "")
    for secret in collect_secret_values(*(secret_values or [])):
        redacted = redacted.replace(secret, REDACTED_VALUE)
    return redacted


def redact_json_like(
    value: object,
    *,
    key_hint: str | None = None,
    extra_sensitive_keys: Iterable[str] | None = None,
    known_secret_values: Iterable[str] | None = None,
    max_items: int | None = None,
) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        items = list(value.items())
        if max_items is not None:
            items = items[:max_items]
        for key, inner_value in items:
            normalized_key = str(key or "")
            if is_sensitive_key(normalized_key, extra_sensitive_keys=extra_sensitive_keys):
                sanitized[normalized_key] = REDACTED_VALUE
            else:
                sanitized[normalized_key] = redact_json_like(
                    inner_value,
                    key_hint=normalized_key,
                    extra_sensitive_keys=extra_sensitive_keys,
                    known_secret_values=known_secret_values,
                    max_items=max_items,
                )
        return sanitized
    if isinstance(value, list):
        items = value[:max_items] if max_items is not None else value
        return [
            redact_json_like(
                item,
                key_hint=key_hint,
                extra_sensitive_keys=extra_sensitive_keys,
                known_secret_values=known_secret_values,
                max_items=max_items,
            )
            for item in items
        ]
    if isinstance(value, tuple):
        items = list(value[:max_items] if max_items is not None else value)
        return [
            redact_json_like(
                item,
                key_hint=key_hint,
                extra_sensitive_keys=extra_sensitive_keys,
                known_secret_values=known_secret_values,
                max_items=max_items,
            )
            for item in items
        ]
    if isinstance(value, str):
        if key_hint and is_sensitive_key(key_hint, extra_sensitive_keys=extra_sensitive_keys):
            return REDACTED_VALUE
        return redact_known_secret_values(value, known_secret_values)
    return value


def redact_mapping(
    mapping: Mapping[str, object] | None,
    *,
    extra_sensitive_keys: Iterable[str] | None = None,
    known_secret_values: Iterable[str] | None = None,
) -> dict[str, object]:
    return redact_json_like(
        dict(mapping or {}),
        extra_sensitive_keys=extra_sensitive_keys,
        known_secret_values=known_secret_values,
    ) or {}


def redact_http_headers(
    headers: Mapping[str, object] | None,
    *,
    allowlist: set[str] | None = None,
    extra_sensitive_keys: Iterable[str] | None = None,
    known_secret_values: Iterable[str] | None = None,
) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in dict(headers or {}).items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        normalized = key_text.lower()
        if allowlist is not None and normalized not in allowlist:
            continue
        if is_sensitive_key(key_text, extra_sensitive_keys=extra_sensitive_keys):
            sanitized[key_text] = REDACTED_VALUE
            continue
        if normalized == "location":
            sanitized[key_text] = redact_url(
                str(value or ""),
                extra_sensitive_query_keys=extra_sensitive_keys,
                known_secret_values=known_secret_values,
            )
            continue
        sanitized[key_text] = redact_known_secret_values(str(value or ""), known_secret_values)
    return sanitized


def redact_url(
    url: str,
    *,
    extra_sensitive_query_keys: Iterable[str] | None = None,
    known_secret_values: Iterable[str] | None = None,
) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if is_sensitive_key(key, extra_sensitive_keys=extra_sensitive_query_keys):
            query_items.append((key, REDACTED_VALUE))
        else:
            query_items.append((key, redact_known_secret_values(value, known_secret_values)))

    hostname = parsed.hostname or ""
    port = parsed.port
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    if parsed.username or parsed.password:
        netloc = f"{REDACTED_VALUE}@{netloc}" if netloc else REDACTED_VALUE

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(query_items, doseq=True),
            redact_known_secret_values(parsed.fragment, known_secret_values),
        )
    )
