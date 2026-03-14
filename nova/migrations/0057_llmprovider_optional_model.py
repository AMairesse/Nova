from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0056_llmprovider_capability_snapshot_messageartifact"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmprovider",
            name="model",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
