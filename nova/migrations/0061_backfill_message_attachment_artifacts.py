import posixpath
import re

from django.db import migrations
from django.db.models import Max


MESSAGE_ATTACHMENT_PATH_RE = re.compile(r"/\.message_attachments/message_(\d+)/")


def _detect_artifact_kind(mime_type, filename):
    normalized_mime = str(mime_type or "").strip().lower()
    normalized_filename = str(filename or "").strip().lower()

    if normalized_mime.startswith("image/"):
        return "image"
    if normalized_mime == "application/pdf" or normalized_filename.endswith(".pdf"):
        return "pdf"
    if normalized_mime.startswith("audio/"):
        return "audio"
    if normalized_mime.startswith("text/") or normalized_mime in {
        "application/json",
        "text/markdown",
    }:
        return "text"
    return "annotation"


def _infer_message_id(user_file):
    if user_file.source_message_id:
        return user_file.source_message_id

    match = MESSAGE_ATTACHMENT_PATH_RE.search(
        str(user_file.original_filename or "")
    )
    if match:
        return int(match.group(1))
    return None


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Message = apps.get_model("nova", "Message")
    MessageArtifact = apps.get_model("nova", "MessageArtifact")
    UserFile = apps.get_model("nova", "UserFile")

    existing_pairs = set(
        MessageArtifact.objects.using(db_alias)
        .filter(direction="input", user_file_id__isnull=False)
        .values_list("message_id", "user_file_id")
    )
    next_order_by_message = {
        row["message_id"]: int(row["max_order"] or -1) + 1
        for row in (
            MessageArtifact.objects.using(db_alias)
            .filter(direction="input")
            .values("message_id")
            .annotate(max_order=Max("order"))
        )
    }

    message_cache = {}
    artifacts_to_create = []
    files_to_update = []

    attachment_files = (
        UserFile.objects.using(db_alias)
        .filter(scope="message_attachment")
        .order_by("created_at", "id")
    )
    for user_file in attachment_files.iterator():
        message_id = _infer_message_id(user_file)
        if not message_id:
            continue

        message = message_cache.get(message_id)
        if message is None:
            message = (
                Message.objects.using(db_alias)
                .filter(id=message_id)
                .first()
            )
            message_cache[message_id] = message
        if message is None:
            continue
        if user_file.user_id != message.user_id:
            continue
        if user_file.thread_id and user_file.thread_id != message.thread_id:
            continue

        if user_file.source_message_id != message_id or user_file.thread_id != message.thread_id:
            files_to_update.append(
                (user_file.id, message_id, message.thread_id)
            )

        pair = (message_id, user_file.id)
        if pair in existing_pairs:
            continue

        label = (
            posixpath.basename(str(user_file.original_filename or "").strip())
            or f"attachment-{user_file.id}"
        )
        order = next_order_by_message.get(message_id, 0)
        artifacts_to_create.append(
            MessageArtifact(
                user_id=message.user_id,
                thread_id=message.thread_id,
                message_id=message_id,
                user_file_id=user_file.id,
                direction="input",
                kind=_detect_artifact_kind(
                    user_file.mime_type,
                    user_file.original_filename,
                ),
                mime_type=user_file.mime_type or "",
                label=label,
                summary_text="",
                search_text=label,
                order=order,
                metadata={"source": "migration_backfill"},
            )
        )
        existing_pairs.add(pair)
        next_order_by_message[message_id] = order + 1

        if len(artifacts_to_create) >= 500:
            MessageArtifact.objects.using(db_alias).bulk_create(
                artifacts_to_create
            )
            artifacts_to_create = []

    if artifacts_to_create:
        MessageArtifact.objects.using(db_alias).bulk_create(artifacts_to_create)

    for file_id, message_id, thread_id in files_to_update:
        UserFile.objects.using(db_alias).filter(id=file_id).update(
            source_message_id=message_id,
            thread_id=thread_id,
        )

    for message in Message.objects.using(db_alias).iterator():
        internal_data = (
            dict(message.internal_data)
            if isinstance(message.internal_data, dict)
            else None
        )
        if not internal_data or "message_attachments" not in internal_data:
            continue

        internal_data.pop("message_attachments", None)
        message.internal_data = internal_data
        message.save(update_fields=["internal_data"])


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0060_remove_messageartifact_published_to_file"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
