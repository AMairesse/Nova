from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0059_messageartifact_published_file"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="messageartifact",
            name="published_to_file",
        ),
    ]
