from __future__ import annotations

import posixpath
from typing import Any, Iterable

from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.UserFile import UserFile


def detect_artifact_kind(mime_type: str | None, filename: str | None = None) -> str:
    normalized_mime = str(mime_type or "").strip().lower()
    normalized_filename = str(filename or "").strip().lower()

    if normalized_mime.startswith("image/"):
        return ArtifactKind.IMAGE
    if normalized_mime == "application/pdf" or normalized_filename.endswith(".pdf"):
        return ArtifactKind.PDF
    if normalized_mime.startswith("audio/"):
        return ArtifactKind.AUDIO
    if normalized_mime.startswith("text/") or normalized_mime in {"application/json", "text/markdown"}:
        return ArtifactKind.TEXT
    return ArtifactKind.ANNOTATION


def build_artifact_label(user_file: UserFile | None, *, fallback: str = "") -> str:
    if user_file is not None:
        return posixpath.basename(user_file.original_filename or "") or fallback
    return fallback or "artifact"


def build_message_artifact_manifest(artifact: MessageArtifact) -> dict[str, Any]:
    return artifact.to_manifest()


def build_message_artifact_manifest_from_user_file(
    user_file: UserFile,
    *,
    direction: str = ArtifactDirection.INPUT,
    kind: str | None = None,
) -> dict[str, Any]:
    artifact_kind = kind or detect_artifact_kind(user_file.mime_type, user_file.original_filename)
    return {
        "message_id": user_file.source_message_id,
        "user_file_id": user_file.id,
        "direction": direction,
        "kind": artifact_kind,
        "mime_type": user_file.mime_type or "",
        "label": build_artifact_label(user_file),
        "summary_text": "",
        "size": int(user_file.size or 0),
        "published_to_file": user_file.scope == UserFile.Scope.THREAD_SHARED,
        "metadata": {},
    }


def normalize_message_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    artifacts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            artifact_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        artifacts.append(
            {
                "id": artifact_id,
                "message_id": item.get("message_id"),
                "user_file_id": item.get("user_file_id"),
                "direction": str(item.get("direction") or "").strip(),
                "kind": str(item.get("kind") or "").strip(),
                "mime_type": str(item.get("mime_type") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "summary_text": str(item.get("summary_text") or "").strip(),
                "size": max(0, size),
                "published_to_file": bool(item.get("published_to_file")),
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            }
        )
    return artifacts


def filter_image_attachment_manifests(artifacts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in artifacts
        if str(artifact.get("kind") or "").strip() == ArtifactKind.IMAGE
    ]
