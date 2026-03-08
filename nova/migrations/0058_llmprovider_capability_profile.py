from django.db import migrations, models


def reset_provider_capability_state(apps, schema_editor):
    LLMProvider = apps.get_model("nova", "LLMProvider")
    LLMProvider.objects.all().update(
        validation_status="untested",
        validated_fingerprint="",
        validation_task_id="",
        validation_requested_fingerprint="",
        capability_profile={},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0057_llmprovider_optional_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmprovider",
            name="capability_profile",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            reset_provider_capability_state,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="llmprovider",
            name="capability_snapshot",
        ),
        migrations.RemoveField(
            model_name="llmprovider",
            name="capability_refreshed_at",
        ),
        migrations.RemoveField(
            model_name="llmprovider",
            name="validated_at",
        ),
        migrations.RemoveField(
            model_name="llmprovider",
            name="validation_capabilities",
        ),
        migrations.RemoveField(
            model_name="llmprovider",
            name="validation_summary",
        ),
    ]
