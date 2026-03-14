from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0055_llmprovider_openrouter"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="llmprovider",
            name="capability_refreshed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="llmprovider",
            name="capability_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="MessageArtifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("direction", models.CharField(choices=[("input", "Input"), ("output", "Output"), ("derived", "Derived")], db_index=True, default="input", max_length=16)),
                ("kind", models.CharField(choices=[("image", "Image"), ("pdf", "PDF"), ("audio", "Audio"), ("text", "Text"), ("annotation", "Annotation")], db_index=True, max_length=16)),
                ("mime_type", models.CharField(blank=True, default="", max_length=100)),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("summary_text", models.TextField(blank=True, default="")),
                ("search_text", models.TextField(blank=True, default="")),
                ("provider_type", models.CharField(blank=True, default="", max_length=32)),
                ("model", models.CharField(blank=True, default="", max_length=120)),
                ("provider_fingerprint", models.CharField(blank=True, default="", max_length=64)),
                ("order", models.PositiveIntegerField(default=0)),
                ("published_to_file", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="nova.message")),
                ("source_artifact", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="derived_artifacts", to="nova.messageartifact")),
                ("thread", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="nova.thread")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="message_artifacts", to=settings.AUTH_USER_MODEL)),
                ("user_file", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="artifacts", to="nova.userfile")),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [
                    models.Index(fields=["thread", "direction", "kind", "created_at"], name="nova_message_thread__f237c3_idx"),
                    models.Index(fields=["message", "direction", "order", "id"], name="nova_message_message_234be7_idx"),
                ],
            },
        ),
    ]
