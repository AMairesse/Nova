from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0074_remove_runtime_engine_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="tool",
            name="available_functions",
        ),
        migrations.RemoveField(
            model_name="tool",
            name="input_schema",
        ),
        migrations.RemoveField(
            model_name="tool",
            name="output_schema",
        ),
        migrations.DeleteModel(
            name="CheckpointLink",
        ),
    ]
