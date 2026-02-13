"""Helpers for default thread titles and generated title normalization."""

from __future__ import annotations

import re


# Backward-compatible default title patterns:
# - legacy: "thread n°42"
# - current: "New thread 42"
_LEGACY_DEFAULT_SUBJECT_RE = re.compile(r"^thread n°\d+$", re.IGNORECASE)
_CURRENT_DEFAULT_SUBJECT_RE = re.compile(r"^new thread \d+$", re.IGNORECASE)


def build_default_thread_subject(thread_count: int) -> str:
    """Return the default title for a new thread."""
    safe_count = max(int(thread_count), 1)
    return f"New thread {safe_count}"


def is_default_thread_subject(subject: str | None) -> bool:
    """Return True when the subject still matches a default placeholder title."""
    text = (subject or "").strip()
    if not text:
        return False
    return bool(
        _LEGACY_DEFAULT_SUBJECT_RE.fullmatch(text)
        or _CURRENT_DEFAULT_SUBJECT_RE.fullmatch(text)
    )


def normalize_generated_thread_title(raw_title: str | None, *, max_length: int = 120) -> str:
    """Clean LLM output and keep only a short single-line title."""
    text = (raw_title or "").strip()
    if not text:
        return ""

    # Keep only the first non-empty line.
    for line in text.splitlines():
        line = line.strip()
        if line:
            text = line
            break
    else:
        return ""

    # Remove wrapping quotes commonly produced by LLMs.
    text = text.strip(" \"'`“”‘’")
    if not text:
        return ""

    if len(text) > max_length:
        text = text[:max_length].rstrip(" .,:;!-")

    # Avoid keeping the default placeholder as a "generated" title.
    if is_default_thread_subject(text):
        return ""
    return text
