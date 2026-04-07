from __future__ import annotations

import logging
import re

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric


logger = logging.getLogger(__name__)

FAILURE_KIND_UNKNOWN_COMMAND = "unknown_command"
FAILURE_KIND_UNSUPPORTED_SYNTAX = "unsupported_syntax"
FAILURE_KIND_PARSE_ERROR = "parse_error"
FAILURE_KIND_INVALID_ARGUMENTS = "invalid_arguments"
FAILURE_KIND_CAPABILITY_DISABLED = "capability_disabled"
FAILURE_KIND_PATH_ERROR = "path_error"
FAILURE_KIND_COMMAND_ERROR = "command_error"

MAX_RECENT_EXAMPLES = 5
MAX_COMMAND_EXAMPLE_LENGTH = 320
MAX_ERROR_LENGTH = 500

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?P<prefix>(?:^|\s)(?:--?(?:api[-_]?key|token|secret|password|authorization)\b(?:\s+|=)))"
    r"(?P<value>(?:\"[^\"]*\"|'[^']*'|[^\s]+))",
    flags=re.IGNORECASE,
)
_AUTH_HEADER_PATTERN = re.compile(
    r"(?P<prefix>\bAuthorization\s*:\s*)(?P<value>[^\s]+)",
    flags=re.IGNORECASE,
)


def sanitize_terminal_command(command: str) -> str:
    sanitized = str(command or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    sanitized = _SENSITIVE_VALUE_PATTERN.sub(r"\g<prefix><redacted>", sanitized)
    sanitized = _AUTH_HEADER_PATTERN.sub(r"\g<prefix><redacted>", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > MAX_COMMAND_EXAMPLE_LENGTH:
        sanitized = f"{sanitized[:MAX_COMMAND_EXAMPLE_LENGTH - 12].rstrip()} ...[truncated]"
    return sanitized


def normalize_head_command(command: str) -> str:
    sanitized = sanitize_terminal_command(command)
    match = re.match(r"^([^\s|<>;&`]+)", sanitized)
    if not match:
        return ""
    token = match.group(1).strip().lower()
    if token == "la":
        return "ls"
    return token


def classify_terminal_failure(message: str) -> str:
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    if normalized.startswith("Unknown command:"):
        return FAILURE_KIND_UNKNOWN_COMMAND
    if normalized.startswith("Command parse error:") or normalized == "Empty command.":
        return FAILURE_KIND_PARSE_ERROR
    if "not supported" in lowered:
        return FAILURE_KIND_UNSUPPORTED_SYNTAX
    if "not enabled for this agent" in lowered:
        return FAILURE_KIND_CAPABILITY_DISABLED
    if normalized.startswith("Usage:") or normalized.startswith("Missing value after"):
        return FAILURE_KIND_INVALID_ARGUMENTS
    if normalized.startswith("Path not found:") or normalized.startswith("Directory not found:"):
        return FAILURE_KIND_PATH_ERROR
    if normalized.startswith("File already exists:") or normalized.startswith("Cannot "):
        return FAILURE_KIND_PATH_ERROR
    return FAILURE_KIND_COMMAND_ERROR


def _record_terminal_command_failure_sync(
    *,
    command: str,
    failure_kind: str,
    error_message: str,
) -> None:
    now = timezone.now()
    sanitized_command = sanitize_terminal_command(command)
    head_command = normalize_head_command(command)
    last_error = str(error_message or "").strip()
    if len(last_error) > MAX_ERROR_LENGTH:
        last_error = f"{last_error[:MAX_ERROR_LENGTH - 12].rstrip()} ...[truncated]"

    with transaction.atomic():
        metric, _created = TerminalCommandFailureMetric.objects.select_for_update().get_or_create(
            bucket_date=timezone.localdate(now),
            head_command=head_command,
            failure_kind=str(failure_kind or "").strip() or FAILURE_KIND_COMMAND_ERROR,
            defaults={
                "count": 0,
                "last_seen_at": now,
                "recent_examples": [],
                "last_error": "",
            },
        )
        metric.count = int(metric.count or 0) + 1
        metric.last_seen_at = now
        metric.last_error = last_error
        examples = [
            str(item).strip()
            for item in list(metric.recent_examples or [])
            if str(item).strip()
        ]
        if sanitized_command:
            examples = [item for item in examples if item != sanitized_command]
            examples.append(sanitized_command)
            examples = examples[-MAX_RECENT_EXAMPLES:]
        metric.recent_examples = examples
        metric.save(
            update_fields=[
                "count",
                "last_seen_at",
                "last_error",
                "recent_examples",
                "updated_at",
            ]
        )


async def record_terminal_command_failure(
    *,
    command: str,
    failure_kind: str,
    error_message: str,
) -> None:
    try:
        await sync_to_async(_record_terminal_command_failure_sync, thread_sensitive=True)(
            command=command,
            failure_kind=failure_kind,
            error_message=error_message,
        )
    except Exception:
        logger.exception(
            "Could not record terminal command failure metric failure_kind=%s head_command=%s",
            failure_kind,
            normalize_head_command(command),
        )
