from __future__ import annotations

from asgiref.sync import async_to_sync

from nova.file_utils import (
    batch_upload_files,
    build_message_attachment_path,
)
from nova.message_artifacts import (
    build_message_artifact_manifest,
    detect_artifact_kind,
    filter_image_attachment_manifests,
    normalize_message_artifacts,
)
from nova.message_attachments import (
    MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY,
    build_message_attachment_metadata,
    format_message_attachment_size_label,
    get_message_attachment_max_audio_size_bytes,
    get_message_attachment_max_document_size_bytes,
    get_message_attachment_max_files,
    get_message_attachment_max_image_size_bytes,
    normalize_message_attachments,
)
from nova.models.MessageArtifact import ArtifactDirection, MessageArtifact
from nova.models.UserFile import UserFile


def upload_message_attachments(thread, user, message, uploaded_files) -> tuple[list[dict], list[str]]:
    uploaded_files = list(uploaded_files or [])
    if not uploaded_files:
        return [], []

    max_files = get_message_attachment_max_files()
    max_image_size = get_message_attachment_max_image_size_bytes()
    if len(uploaded_files) > max_files:
        return [], [f"You can attach up to {max_files} attachments per message."]

    file_data = []
    max_upload_size = 0
    for uploaded_file in uploaded_files:
        guessed_kind = detect_artifact_kind(
            getattr(uploaded_file, "content_type", None),
            getattr(uploaded_file, "name", None),
        )
        if guessed_kind == "image":
            max_size_bytes = max_image_size
            too_large_label = "Image"
        elif guessed_kind == "pdf":
            max_size_bytes = get_message_attachment_max_document_size_bytes()
            too_large_label = "PDF"
        elif guessed_kind == "audio":
            max_size_bytes = get_message_attachment_max_audio_size_bytes()
            too_large_label = "Audio"
        else:
            return [], [f"Unsupported attachment type: {uploaded_file.name}"]

        if uploaded_file.size > max_size_bytes:
            max_size_label = format_message_attachment_size_label(max_size_bytes)
            return [], [f"{too_large_label} too large: {uploaded_file.name} ({max_size_label} max)"]
        try:
            content = uploaded_file.read()
        except Exception as exc:
            return [], [f"Attachment upload failed while reading {uploaded_file.name}: {exc}"]

        file_data.append(
            {
                "path": build_message_attachment_path(message.id, uploaded_file.name),
                "content": content,
            }
        )
        max_upload_size = max(max_upload_size, max_size_bytes)

    created_files, errors = async_to_sync(batch_upload_files)(
        thread,
        user,
        file_data,
        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        source_message=message,
        max_file_size=max_upload_size or max_image_size,
        allowed_mime_types=["application/pdf"],
        allowed_mime_prefixes=("image/", "audio/"),
    )
    _create_message_artifacts_for_uploaded_files(message, created_files)
    return build_message_attachment_metadata(created_files), errors


def _create_message_artifacts_for_uploaded_files(message, created_files: list[dict]) -> list[MessageArtifact]:
    file_ids = []
    for item in created_files:
        try:
            file_ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            continue

    if not file_ids:
        return []

    user_files = {
        user_file.id: user_file
        for user_file in UserFile.objects.filter(
            id__in=file_ids,
            source_message=message,
            thread=message.thread,
            user=message.user,
        )
    }

    artifacts_to_create = []
    for index, item in enumerate(created_files):
        try:
            file_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        user_file = user_files.get(file_id)
        if user_file is None:
            continue

        artifacts_to_create.append(
            MessageArtifact(
                user=message.user,
                thread=message.thread,
                message=message,
                user_file=user_file,
                direction=ArtifactDirection.INPUT,
                kind=detect_artifact_kind(user_file.mime_type, user_file.original_filename),
                mime_type=user_file.mime_type or "",
                label=user_file.original_filename.rsplit("/", 1)[-1],
                search_text=user_file.original_filename.rsplit("/", 1)[-1],
                order=index,
                metadata={"source": "message_attachment"},
            )
        )

    return MessageArtifact.objects.bulk_create(artifacts_to_create)


def annotate_user_message(message) -> None:
    internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
    file_ids = internal_data.get("file_ids")
    if isinstance(file_ids, list):
        message.file_count = len(file_ids)
    else:
        message.file_count = 0

    artifact_manifests: list[dict] = []
    prefetched_artifacts = None
    prefetched_cache = getattr(message, "_prefetched_objects_cache", None)
    if isinstance(prefetched_cache, dict):
        prefetched_artifacts = prefetched_cache.get("artifacts")

    if prefetched_artifacts is not None:
        try:
            ordered_artifacts = sorted(
                prefetched_artifacts,
                key=lambda artifact: (
                    str(getattr(artifact, "direction", "") or ""),
                    int(getattr(artifact, "order", 0) or 0),
                    getattr(artifact, "created_at", None),
                    int(getattr(artifact, "id", 0) or 0),
                ),
            )
            artifact_manifests = [
                build_message_artifact_manifest(artifact)
                for artifact in ordered_artifacts
            ]
        except Exception:
            artifact_manifests = []
    else:
        related_artifacts = getattr(message, "artifacts", None)
        if related_artifacts is not None:
            try:
                artifact_manifests = [
                    build_message_artifact_manifest(artifact)
                    for artifact in related_artifacts.select_related("user_file").order_by("direction", "order",
                                                                                           "created_at", "id")
                ]
            except Exception:
                artifact_manifests = []

    if not artifact_manifests:
        legacy_attachments = normalize_message_attachments(
            internal_data.get(MESSAGE_ATTACHMENT_INTERNAL_DATA_KEY)
        )
        artifact_manifests = [
            {
                "id": attachment["id"],
                "message_id": getattr(message, "id", None),
                "user_file_id": attachment["id"],
                "direction": ArtifactDirection.INPUT,
                "kind": detect_artifact_kind(attachment.get("mime_type"), attachment.get("filename")),
                "mime_type": attachment.get("mime_type") or "",
                "label": attachment.get("filename") or "",
                "summary_text": "",
                "size": int(attachment.get("size") or 0),
                "published_to_file": False,
                "metadata": {"scope": attachment.get("scope") or "", "legacy": True},
            }
            for attachment in legacy_attachments
        ]

    message.message_artifacts = normalize_message_artifacts(artifact_manifests)
    message.message_attachments = filter_image_attachment_manifests(message.message_artifacts)
    message.message_attachment_count = len(message.message_artifacts)
