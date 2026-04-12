from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0075_remove_checkpointlink_and_tool_legacy_fields"),
    ]

    operations = [
        migrations.DeleteModel(
            name="MessageArtifact",
        ),
    ]
