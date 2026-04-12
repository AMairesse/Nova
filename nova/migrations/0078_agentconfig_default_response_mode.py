from django.db import migrations, models


def backfill_image_agent_default_response_mode(apps, schema_editor):
    AgentConfig = apps.get_model("nova", "AgentConfig")
    AgentConfig.objects.filter(name="Image Agent").update(default_response_mode="image")


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0077_remove_langfuse_userparameters"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentconfig",
            name="default_response_mode",
            field=models.CharField(
                choices=[("text", "Text"), ("image", "Image"), ("audio", "Audio")],
                default="text",
                help_text="Default output type used when the user has not explicitly selected a response mode.",
                max_length=16,
                verbose_name="Default response mode",
            ),
        ),
        migrations.RunPython(
            backfill_image_agent_default_response_mode,
            migrations.RunPython.noop,
        ),
    ]
