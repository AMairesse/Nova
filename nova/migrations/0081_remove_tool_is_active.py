from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0080_tool_capability_singletons"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="tool",
            name="is_active",
        ),
    ]
