from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0061_backfill_message_attachment_artifacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="execution_trace",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
