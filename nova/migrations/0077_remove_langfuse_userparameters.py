from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0076_delete_messageartifact_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userparameters",
            name="allow_langfuse",
        ),
        migrations.RemoveField(
            model_name="userparameters",
            name="langfuse_public_key",
        ),
        migrations.RemoveField(
            model_name="userparameters",
            name="langfuse_secret_key",
        ),
        migrations.RemoveField(
            model_name="userparameters",
            name="langfuse_host",
        ),
    ]
