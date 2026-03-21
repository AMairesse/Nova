from __future__ import annotations

import logging
import posixpath
from typing import Any, Iterable

import httpx
from asgiref.sync import sync_to_async
from django.urls import reverse

from nova.file_utils import batch_upload_files, download_file_content
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.UserFile import UserFile

logger = logging.getLogger(__name__)


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
    manifest = artifact.to_manifest()
    if artifact.pk and (artifact.user_file_id or artifact.summary_text):
        content_url = reverse("artifact_content", args=[artifact.pk])
        manifest["content_url"] = content_url
        manifest["preview_url"] = content_url
    return manifest


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
                "content_url": str(item.get("content_url") or "").strip(),
                "preview_url": str(item.get("preview_url") or "").strip(),
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


async def _resolve_publish_source_user_file(artifact: MessageArtifact) -> UserFile | None:
    artifact_chain_ids: list[int] = []
    current_artifact = artifact

    while current_artifact and current_artifact.pk and current_artifact.pk not in artifact_chain_ids:
        artifact_chain_ids.append(current_artifact.pk)

        if current_artifact.user_file_id:
            def _load_user_file():
                return UserFile.objects.filter(
                    id=current_artifact.user_file_id,
                    user=current_artifact.user,
                    thread=current_artifact.thread,
                ).first()

            user_file = await sync_to_async(_load_user_file, thread_sensitive=True)()
            if user_file is not None:
                return user_file

        if not current_artifact.source_artifact_id:
            break

        def _load_source_artifact():
            return (
                MessageArtifact.objects.select_related("user_file", "source_artifact")
                .filter(
                    id=current_artifact.source_artifact_id,
                    user=artifact.user,
                    thread=artifact.thread,
                )
                .first()
            )

        current_artifact = await sync_to_async(_load_source_artifact, thread_sensitive=True)()

    return None


async def publish_artifact_to_files(
    artifact: MessageArtifact,
    *,
    filename: str = "",
) -> tuple[int | None, list[str]]:
    try:
        target_name = (filename or "").strip() or artifact.filename
        source_user_file = await _resolve_publish_source_user_file(artifact)

        if source_user_file is not None:
            try:
                content = await download_file_content(source_user_file)
            except Exception as exc:
                logger.warning(
                    "Direct artifact binary download failed for artifact %s: %s",
                    artifact.id,
                    exc,
                )
                try:
                    download_url = await sync_to_async(
                        source_user_file.get_download_url,
                        thread_sensitive=True,
                    )(expires_in=3600)
                except Exception as url_exc:
                    logger.exception(
                        "Artifact fallback download URL generation failed for artifact %s",
                        artifact.id,
                    )
                    return None, [f"Artifact publish failed: {url_exc}"]

                if not download_url:
                    return None, ["Artifact publish failed: no download URL could be generated."]

                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                        response = await client.get(download_url)
                    response.raise_for_status()
                    content = response.content
                except Exception as fallback_exc:
                    logger.exception(
                        "Artifact fallback binary download failed for artifact %s",
                        artifact.id,
                    )
                    return None, [f"Artifact publish failed: {fallback_exc}"]
            explicit_mime_type = artifact.mime_type or source_user_file.mime_type or ""
            if not target_name:
                target_name = build_artifact_label(source_user_file, fallback=f"artifact-{artifact.id}")
        elif artifact.summary_text:
            content = artifact.summary_text.encode("utf-8")
            explicit_mime_type = artifact.mime_type or "text/plain"
            if not target_name.endswith(".txt"):
                target_name = f"{target_name}.txt"
        else:
            return None, ["Artifact cannot be published because it has no binary or text content."]

        created, errors = await batch_upload_files(
            artifact.thread,
            artifact.user,
            [
                {
                    "path": f"/generated/{posixpath.basename(target_name)}",
                    "content": content,
                    "mime_type": explicit_mime_type,
                }
            ],
            scope=UserFile.Scope.THREAD_SHARED,
        )
        if errors and not created:
            return None, errors
        if not created:
            return None, ["Artifact publish did not create a file."]

        file_id = created[0].get("id") if created else None
        published_file = None
        if file_id:
            def _load_published_file():
                return UserFile.objects.filter(
                    id=file_id,
                    user=artifact.user,
                    thread=artifact.thread,
                    scope=UserFile.Scope.THREAD_SHARED,
                ).first()

            published_file = await sync_to_async(_load_published_file, thread_sensitive=True)()

        artifact.published_file = published_file
        await sync_to_async(artifact.save, thread_sensitive=True)(
            update_fields=["published_file", "updated_at"]
        )
        return file_id, errors
    except Exception as exc:
        logger.exception("Unexpected artifact publish failure for artifact %s", artifact.id)
        return None, [f"Artifact publish failed: {exc}"]


def clone_artifact_for_message(
    source_artifact: MessageArtifact,
    *,
    message,
    direction: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MessageArtifact:
    cloned_metadata = dict(source_artifact.metadata or {})
    cloned_metadata.update(metadata or {})
    return MessageArtifact.objects.create(
        user=message.user,
        thread=message.thread,
        message=message,
        user_file=source_artifact.user_file,
        source_artifact=source_artifact,
        direction=direction or source_artifact.direction,
        kind=source_artifact.kind,
        mime_type=source_artifact.mime_type or "",
        label=source_artifact.filename,
        summary_text=source_artifact.summary_text or "",
        search_text=source_artifact.search_text or source_artifact.filename,
        provider_type=source_artifact.provider_type or "",
        model=source_artifact.model or "",
        provider_fingerprint=source_artifact.provider_fingerprint or "",
        order=source_artifact.order,
        published_file=None,
        metadata=cloned_metadata,
    )
