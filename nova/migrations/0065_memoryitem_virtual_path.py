from django.db import migrations, models
from django.db.models import Q


def backfill_memory_virtual_paths(apps, schema_editor):
    MemoryTheme = apps.get_model("nova", "MemoryTheme")
    MemoryItem = apps.get_model("nova", "MemoryItem")

    for item in MemoryItem.objects.select_related("theme").order_by("user_id", "id"):
        theme = item.theme
        if theme is None:
            theme, _ = MemoryTheme.objects.get_or_create(
                user_id=item.user_id,
                slug="general",
                defaults={"display_name": "General", "description": ""},
            )
            item.theme = theme
        item.virtual_path = f"/memory/{theme.slug}/{item.id}.md"
        item.save(update_fields=["theme", "virtual_path", "updated_at"])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("nova", "0064_agentconfig_runtime_engine_agentthreadsession"),
    ]

    operations = [
        migrations.AddField(
            model_name="memoryitem",
            name="virtual_path",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(backfill_memory_virtual_paths, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="memoryitem",
            constraint=models.UniqueConstraint(
                condition=Q(status="active") & ~Q(virtual_path=""),
                fields=("user", "virtual_path"),
                name="uniq_mem_item_u_vpath_a",
            ),
        ),
        migrations.AddIndex(
            model_name="memoryitem",
            index=models.Index(fields=["user", "virtual_path"], name="idx_mem_item_u_vpath"),
        ),
    ]
