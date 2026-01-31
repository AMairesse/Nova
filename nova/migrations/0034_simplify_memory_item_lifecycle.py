from django.db import migrations, models


def forwards_simplify_status(apps, schema_editor):
    MemoryItem = apps.get_model("nova", "MemoryItem")
    # If older statuses exist in DB, normalize them to the remaining set.
    MemoryItem.objects.filter(status="superseded").update(status="archived")


def backwards_restore_status(apps, schema_editor):
    # No-op: we can't reliably infer which archived items used to be superseded.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0033_memory_embedding_vector_index"),
    ]

    operations = [
        migrations.RunPython(forwards_simplify_status, backwards_restore_status),
        migrations.RemoveField(
            model_name="memoryitem",
            name="supersedes",
        ),
        migrations.AlterField(
            model_name="memoryitem",
            name="status",
            field=models.CharField(
                choices=[("active", "active"), ("archived", "archived")],
                default="active",
                max_length=20,
            ),
        ),
    ]
