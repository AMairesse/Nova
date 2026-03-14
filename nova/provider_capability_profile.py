"""Unified capability profile helpers for provider metadata and active verification."""

from __future__ import annotations

from copy import deepcopy

from django.utils.translation import gettext_lazy as _


CAPABILITY_PROFILE_SCHEMA_VERSION = 1


CAPABILITY_PROFILE_GROUPS: dict[str, tuple[str, ...]] = {
    "inputs": ("text", "image", "pdf", "audio"),
    "outputs": ("text", "image", "audio"),
    "operations": (
        "chat",
        "streaming",
        "tools",
        "vision",
        "structured_output",
        "reasoning",
        "image_generation",
        "audio_generation",
    ),
}

PROBED_OPERATION_KEYS = ("chat", "streaming", "tools", "vision")
PROBED_INPUT_KEYS = ("pdf",)

CAPABILITY_PROFILE_LABELS: dict[str, dict[str, object]] = {
    "inputs": {
        "text": _("Text"),
        "image": _("Image"),
        "pdf": _("PDF"),
        "audio": _("Audio"),
    },
    "outputs": {
        "text": _("Text"),
        "image": _("Image"),
        "audio": _("Audio"),
    },
    "operations": {
        "chat": _("Chat"),
        "streaming": _("Streaming"),
        "tools": _("Tools"),
        "vision": _("Vision"),
        "structured_output": _("Structured output"),
        "reasoning": _("Reasoning"),
        "image_generation": _("Image generation"),
        "audio_generation": _("Audio generation"),
    },
}

CAPABILITY_MESSAGE_LABELS: dict[str, dict[str, str]] = {
    "inputs": {
        "text": "text input",
        "image": "image input",
        "pdf": "PDF input",
        "audio": "audio input",
    },
    "outputs": {
        "text": "text output",
        "image": "image output",
        "audio": "audio output",
    },
    "operations": {
        "chat": "chat",
        "streaming": "streaming",
        "tools": "tool calling",
        "vision": "vision",
        "structured_output": "structured output",
        "reasoning": "reasoning",
        "image_generation": "image generation",
        "audio_generation": "audio generation",
    },
}

DERIVED_VERIFICATION_MAPPINGS: dict[str, tuple[dict[str, object], ...]] = {
    "chat": (
        {
            "group": "inputs",
            "capability": "text",
            "statuses": {"pass", "unsupported"},
            "message": {
                "pass": "Verified through chat probe.",
                "unsupported": "Chat probe indicates text input is unsupported.",
            },
        },
        {
            "group": "outputs",
            "capability": "text",
            "statuses": {"pass", "unsupported"},
            "message": {
                "pass": "Verified through chat probe.",
                "unsupported": "Chat probe indicates text output is unsupported.",
            },
        },
    ),
    "streaming": (
        {
            "group": "outputs",
            "capability": "text",
            "statuses": {"pass"},
            "message": {
                "pass": "Verified through streaming probe.",
            },
        },
    ),
    "vision": (
        {
            "group": "inputs",
            "capability": "image",
            "statuses": {"pass", "fail", "unsupported"},
            "message": {
                "pass": "Verified through vision probe.",
                "fail": "Vision probe failed while exercising image input.",
                "unsupported": "Vision probe indicates image input is unsupported.",
            },
        },
    ),
    "image_generation": (
        {
            "group": "outputs",
            "capability": "image",
            "statuses": {"pass", "fail", "unsupported"},
            "message": {
                "pass": "Verified through image generation probe.",
                "fail": "Image generation probe failed.",
                "unsupported": "Image generation probe indicates image output is unsupported.",
            },
        },
    ),
    "audio_generation": (
        {
            "group": "outputs",
            "capability": "audio",
            "statuses": {"pass", "fail", "unsupported"},
            "message": {
                "pass": "Verified through audio generation probe.",
                "fail": "Audio generation probe failed.",
                "unsupported": "Audio generation probe indicates audio output is unsupported.",
            },
        },
    ),
}

DECLARED_STATUS_VALUES = {"pass", "unsupported", "unknown"}
VERIFIED_STATUS_VALUES = {"pass", "fail", "unsupported", "not_run"}
EFFECTIVE_STATUS_VALUES = {"pass", "fail", "unsupported", "unknown"}
EFFECTIVE_SOURCE_VALUES = {"declared", "verified", "merged", "none"}

CAPABILITY_EFFECTIVE_STATUS_LABELS = {
    "pass": _("Available"),
    "fail": _("Failed"),
    "unsupported": _("Unavailable"),
    "unknown": _("Unknown"),
}

CAPABILITY_EFFECTIVE_STATUS_BADGE_CLASSES = {
    "pass": "text-bg-success",
    "fail": "text-bg-danger",
    "unsupported": "text-bg-warning",
    "unknown": "text-bg-secondary",
}

CAPABILITY_SOURCE_LABELS = {
    "declared": _("Declared"),
    "verified": _("Verified"),
    "merged": _("Merged"),
    "none": _("Unknown"),
}


def _blank_entry() -> dict:
    return {
        "declared_status": "unknown",
        "verified_status": "not_run",
        "effective_status": "unknown",
        "effective_source": "none",
        "declared_message": "",
        "verified_message": "",
        "effective_message": "",
        "verified_latency_ms": None,
    }


def empty_capability_profile(fingerprint: str = "") -> dict:
    return {
        "schema_version": CAPABILITY_PROFILE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "summary": "",
        "metadata_source_label": "",
        "metadata_checked_at": None,
        "probe_checked_at": None,
        "inputs": {key: _blank_entry() for key in CAPABILITY_PROFILE_GROUPS["inputs"]},
        "outputs": {key: _blank_entry() for key in CAPABILITY_PROFILE_GROUPS["outputs"]},
        "operations": {key: _blank_entry() for key in CAPABILITY_PROFILE_GROUPS["operations"]},
        "limits": {},
        "model_state": {},
    }


def ensure_capability_profile(profile: dict | None) -> dict:
    base = empty_capability_profile(
        str((profile or {}).get("fingerprint") or "")
    )
    if not isinstance(profile, dict):
        return base

    schema_version = profile.get("schema_version")
    if isinstance(schema_version, int) and schema_version > 0:
        base["schema_version"] = schema_version

    for key in ("summary", "metadata_source_label", "metadata_checked_at", "probe_checked_at"):
        value = profile.get(key)
        if isinstance(value, str) or value is None:
            base[key] = value

    for key in ("limits", "model_state"):
        value = profile.get(key)
        if isinstance(value, dict):
            base[key] = deepcopy(value)

    for group_name, group_keys in CAPABILITY_PROFILE_GROUPS.items():
        raw_group = profile.get(group_name)
        if not isinstance(raw_group, dict):
            continue
        for capability_key in group_keys:
            raw_entry = raw_group.get(capability_key)
            if not isinstance(raw_entry, dict):
                continue

            entry = base[group_name][capability_key]
            declared_status = str(raw_entry.get("declared_status") or "").strip().lower()
            verified_status = str(raw_entry.get("verified_status") or "").strip().lower()
            effective_status = str(raw_entry.get("effective_status") or "").strip().lower()
            effective_source = str(raw_entry.get("effective_source") or "").strip().lower()

            entry["declared_status"] = (
                declared_status if declared_status in DECLARED_STATUS_VALUES else "unknown"
            )
            entry["verified_status"] = (
                verified_status if verified_status in VERIFIED_STATUS_VALUES else "not_run"
            )
            entry["effective_status"] = (
                effective_status if effective_status in EFFECTIVE_STATUS_VALUES else "unknown"
            )
            entry["effective_source"] = (
                effective_source if effective_source in EFFECTIVE_SOURCE_VALUES else "none"
            )
            entry["declared_message"] = str(raw_entry.get("declared_message") or "")
            entry["verified_message"] = str(raw_entry.get("verified_message") or "")
            entry["effective_message"] = str(raw_entry.get("effective_message") or "")
            latency = raw_entry.get("verified_latency_ms")
            entry["verified_latency_ms"] = latency if isinstance(latency, int) else None

    return base


def _normalize_declared_status(value) -> str:
    status = str(value or "").strip().lower()
    return status if status in DECLARED_STATUS_VALUES else "unknown"


def _normalize_verified_status(value) -> str:
    status = str(value or "").strip().lower()
    return status if status in VERIFIED_STATUS_VALUES else "not_run"


def _build_declared_message(group_name: str, capability_key: str, status: str, source_label: str) -> str:
    if status == "unknown":
        return ""

    capability_label = CAPABILITY_MESSAGE_LABELS[group_name][capability_key]
    if status == "pass":
        return f"{source_label} declares {capability_label} support."
    return f"{source_label} does not declare {capability_label} support."


def _recompute_effective_entry(group_name: str, capability_key: str, entry: dict) -> None:
    declared_status = _normalize_declared_status(entry.get("declared_status"))
    verified_status = _normalize_verified_status(entry.get("verified_status"))

    if verified_status in {"pass", "fail", "unsupported"}:
        if (
            verified_status in {"pass", "unsupported"}
            and declared_status == verified_status
        ):
            entry["effective_source"] = "merged"
        else:
            entry["effective_source"] = "verified"
        entry["effective_status"] = verified_status
        entry["effective_message"] = (
            entry.get("verified_message")
            or entry.get("declared_message")
            or ""
        )
        return

    if declared_status in {"pass", "unsupported"}:
        entry["effective_source"] = "declared"
        entry["effective_status"] = declared_status
        entry["effective_message"] = entry.get("declared_message") or ""
        return

    entry["effective_source"] = "none"
    entry["effective_status"] = "unknown"
    entry["effective_message"] = ""


def _apply_derived_verification(profile: dict) -> None:
    operations = profile.get("operations") or {}
    for operation_key, mappings in DERIVED_VERIFICATION_MAPPINGS.items():
        operation_entry = operations.get(operation_key)
        if not isinstance(operation_entry, dict):
            continue

        operation_status = _normalize_verified_status(operation_entry.get("verified_status"))
        if operation_status == "not_run":
            continue

        for mapping in mappings:
            allowed_statuses = mapping.get("statuses") or set()
            if operation_status not in allowed_statuses:
                continue

            target_group = str(mapping.get("group") or "")
            target_key = str(mapping.get("capability") or "")
            target_entry = ((profile.get(target_group) or {}).get(target_key)) if isinstance(profile.get(target_group), dict) else None
            if not isinstance(target_entry, dict):
                continue

            if _normalize_verified_status(target_entry.get("verified_status")) in {"pass", "fail", "unsupported"}:
                continue

            target_entry["verified_status"] = operation_status
            message_map = mapping.get("message") or {}
            target_entry["verified_message"] = str(
                message_map.get(operation_status)
                or operation_entry.get("verified_message")
                or ""
            )
            target_entry["verified_latency_ms"] = operation_entry.get("verified_latency_ms")


def _recompute_profile(profile: dict) -> dict:
    _apply_derived_verification(profile)
    for group_name, group_keys in CAPABILITY_PROFILE_GROUPS.items():
        for capability_key in group_keys:
            _recompute_effective_entry(group_name, capability_key, profile[group_name][capability_key])
    profile["summary"] = build_capability_profile_summary(profile)
    return profile


def merge_declared_capabilities(
    existing_profile: dict | None,
    declared_fragment: dict | None,
    *,
    fingerprint: str,
    checked_at_iso: str,
) -> dict:
    profile = ensure_capability_profile(
        existing_profile
        if isinstance(existing_profile, dict) and existing_profile.get("fingerprint") == fingerprint
        else {}
    )
    declared_fragment = declared_fragment if isinstance(declared_fragment, dict) else {}

    profile["fingerprint"] = fingerprint
    profile["schema_version"] = CAPABILITY_PROFILE_SCHEMA_VERSION
    profile["metadata_source_label"] = str(declared_fragment.get("metadata_source_label") or "")
    profile["metadata_checked_at"] = checked_at_iso
    profile["limits"] = deepcopy(declared_fragment.get("limits") or {})
    profile["model_state"] = deepcopy(declared_fragment.get("model_state") or {})

    for group_name, group_keys in CAPABILITY_PROFILE_GROUPS.items():
        raw_group = declared_fragment.get(group_name)
        raw_group = raw_group if isinstance(raw_group, dict) else {}
        for capability_key in group_keys:
            entry = profile[group_name][capability_key]
            status = _normalize_declared_status(raw_group.get(capability_key))
            entry["declared_status"] = status
            entry["declared_message"] = _build_declared_message(
                group_name,
                capability_key,
                status,
                profile["metadata_source_label"] or "Provider metadata",
            )

    return _recompute_profile(profile)


def merge_verified_operations(
    existing_profile: dict | None,
    verified_operations: dict | None,
    *,
    fingerprint: str,
    checked_at_iso: str,
) -> dict:
    return merge_verified_capabilities(
        existing_profile,
        {"verified_operations": verified_operations or {}},
        fingerprint=fingerprint,
        checked_at_iso=checked_at_iso,
    )


def merge_verified_capabilities(
    existing_profile: dict | None,
    verification_fragment: dict | None,
    *,
    fingerprint: str,
    checked_at_iso: str,
) -> dict:
    profile = ensure_capability_profile(
        existing_profile
        if isinstance(existing_profile, dict) and existing_profile.get("fingerprint") == fingerprint
        else {}
    )
    verification_fragment = (
        verification_fragment if isinstance(verification_fragment, dict) else {}
    )
    verified_operations = verification_fragment.get("verified_operations")
    verified_operations = verified_operations if isinstance(verified_operations, dict) else {}
    verified_inputs = verification_fragment.get("verified_inputs")
    verified_inputs = verified_inputs if isinstance(verified_inputs, dict) else {}

    profile["fingerprint"] = fingerprint
    profile["schema_version"] = CAPABILITY_PROFILE_SCHEMA_VERSION
    profile["probe_checked_at"] = checked_at_iso

    for capability_key in PROBED_OPERATION_KEYS:
        raw_entry = verified_operations.get(capability_key)
        raw_entry = raw_entry if isinstance(raw_entry, dict) else {}
        entry = profile["operations"][capability_key]
        entry["verified_status"] = _normalize_verified_status(raw_entry.get("status"))
        entry["verified_message"] = str(raw_entry.get("message") or "")
        latency = raw_entry.get("latency_ms")
        entry["verified_latency_ms"] = latency if isinstance(latency, int) else None

    for capability_key in PROBED_INPUT_KEYS:
        raw_entry = verified_inputs.get(capability_key)
        raw_entry = raw_entry if isinstance(raw_entry, dict) else {}
        entry = profile["inputs"][capability_key]
        entry["verified_status"] = _normalize_verified_status(raw_entry.get("status"))
        entry["verified_message"] = str(raw_entry.get("message") or "")
        latency = raw_entry.get("latency_ms")
        entry["verified_latency_ms"] = latency if isinstance(latency, int) else None

    return _recompute_profile(profile)


def build_capability_profile_summary(profile: dict | None) -> str:
    profile = ensure_capability_profile(profile)

    parts: list[str] = []
    unavailable: list[str] = []
    for group_name, prefix in (
        ("inputs", "Inputs"),
        ("outputs", "Outputs"),
        ("operations", "Operations"),
    ):
        labels = [
            str(CAPABILITY_PROFILE_LABELS[group_name][capability_key]).lower()
            for capability_key, entry in profile[group_name].items()
            if entry.get("effective_status") == "pass"
        ]
        if labels:
            parts.append(f"{prefix}: {', '.join(labels)}.")
        unavailable.extend(
            str(CAPABILITY_PROFILE_LABELS[group_name][capability_key]).lower()
            for capability_key, entry in profile[group_name].items()
            if entry.get("effective_status") in {"fail", "unsupported"}
        )

    if unavailable:
        parts.append(f"Unavailable: {', '.join(unavailable)}.")

    if profile.get("metadata_source_label"):
        parts.append(f"Metadata: {profile['metadata_source_label']}.")
    if profile.get("probe_checked_at"):
        parts.append("Verification checked.")

    return " ".join(parts)
