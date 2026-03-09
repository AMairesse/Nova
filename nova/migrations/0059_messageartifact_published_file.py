import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0058_llmprovider_capability_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="messageartifact",
            name="published_file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="published_artifacts",
                to="nova.userfile",
            ),
        ),
    ]
