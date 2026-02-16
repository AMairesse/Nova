"""Helpers for aggregating multiple instances of the same builtin tool module."""

from __future__ import annotations

from typing import Iterable, Sequence


def normalize_instance_key(value: str | None) -> str:
    """Normalize a user-facing instance key for tolerant lookup."""
    return str(value or "").strip().casefold()


def dedupe_instance_labels(
    raw_labels: Sequence[str | None],
    *,
    default_label: str = "Instance",
) -> list[str]:
    """Return deterministic, case-insensitive unique labels.

    Duplicates are suffixed with " #N" while preserving input order.
    """
    used: set[str] = set()
    out: list[str] = []

    for idx, raw in enumerate(raw_labels, start=1):
        base = str(raw or "").strip() or f"{default_label} {idx}"
        candidate = base
        suffix = 2
        while normalize_instance_key(candidate) in used:
            candidate = f"{base} #{suffix}"
            suffix += 1
        used.add(normalize_instance_key(candidate))
        out.append(candidate)

    return out


def format_invalid_instance_message(
    *,
    selector_name: str,
    value: str | None,
    available_labels: Iterable[str],
) -> str:
    """Build a deterministic invalid-selector error message."""
    available = [label for label in available_labels if str(label or "").strip()]
    available_part = ", ".join(available) if available else "none"
    requested = str(value or "").strip() or "<empty>"
    return (
        f"Unknown {selector_name} '{requested}'. "
        f"Available {selector_name} values: {available_part}."
    )


def build_selector_schema(
    *,
    selector_name: str,
    labels: Sequence[str],
    description: str,
) -> dict:
    """Build a JSON-schema property for selecting an aggregated instance."""
    return {
        "type": "string",
        "enum": list(labels),
        "description": description,
    }
