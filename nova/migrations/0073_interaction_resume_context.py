from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0072_remove_toolcredential_custom_auth"),
    ]

    operations = [
        migrations.AddField(
            model_name="interaction",
            name="resume_context",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
