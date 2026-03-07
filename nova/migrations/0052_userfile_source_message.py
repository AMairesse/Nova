from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0051_userfile_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="userfile",
            name="source_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="attached_files",
                to="nova.message",
            ),
        ),
    ]
