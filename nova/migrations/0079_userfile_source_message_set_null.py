from django.db import migrations, models
import django.db.models.deletion


def backfill_userfile_source_message(apps, schema_editor):
    Message = apps.get_model("nova", "Message")
    UserFile = apps.get_model("nova", "UserFile")
    db_alias = schema_editor.connection.alias

    candidates: dict[int, tuple[int, int, int | None]] = {}
    ambiguous_ids: set[int] = set()

    for message in Message.objects.using(db_alias).order_by("id").iterator():
        internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
        file_ids = internal_data.get("file_ids") or []
        if not isinstance(file_ids, list):
            continue

        seen_in_message: set[int] = set()
        for raw_file_id in file_ids:
            try:
                file_id = int(raw_file_id)
            except (TypeError, ValueError):
                continue
            if file_id in seen_in_message or file_id in ambiguous_ids:
                continue
            seen_in_message.add(file_id)

            payload = (message.id, message.user_id, message.thread_id)
            previous = candidates.get(file_id)
            if previous is None:
                candidates[file_id] = payload
                continue
            if previous != payload:
                ambiguous_ids.add(file_id)
                candidates.pop(file_id, None)

    for file_id, (message_id, user_id, thread_id) in candidates.items():
        UserFile.objects.using(db_alias).filter(
            id=file_id,
            user_id=user_id,
            thread_id=thread_id,
            scope="thread_shared",
            source_message_id__isnull=True,
        ).update(source_message_id=message_id)


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0078_agentconfig_default_response_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userfile",
            name="source_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attached_files",
                to="nova.message",
            ),
        ),
        migrations.RunPython(
            backfill_userfile_source_message,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
