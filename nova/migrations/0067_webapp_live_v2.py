from django.db import migrations, models


def purge_legacy_webapps(apps, schema_editor):
    WebApp = apps.get_model("nova", "WebApp")
    WebApp.objects.all().delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("nova", "0066_memory_documents_v2"),
    ]

    operations = [
        migrations.RunPython(purge_legacy_webapps, migrations.RunPython.noop),
        migrations.AddField(
            model_name="webapp",
            name="entry_path",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="webapp",
            name="source_root",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="webapp",
            index=models.Index(fields=["thread", "source_root"], name="nova_webapp_thread_source_idx"),
        ),
        migrations.DeleteModel(
            name="WebAppFile",
        ),
    ]
