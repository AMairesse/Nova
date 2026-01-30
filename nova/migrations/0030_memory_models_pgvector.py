from django.conf import settings
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations, models
from django.db.models.deletion import CASCADE, SET_NULL

import pgvector.django.vector


class Migration(migrations.Migration):
    dependencies = [
        ("nova", "0029_add_summarization_fields_to_agentconfig"),
    ]

    operations = [
        # Enable pgvector
        CreateExtension("vector"),

        migrations.CreateModel(
            name="MemoryTheme",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=80)),
                ("display_name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=CASCADE, related_name="memory_themes", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "slug"], name="idx_memory_theme_user_slug"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="memorytheme",
            constraint=models.UniqueConstraint(fields=("user", "slug"), name="uniq_memory_theme_user_slug"),
        ),

        migrations.CreateModel(
            name="MemoryItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("preference", "preference"),
                            ("fact", "fact"),
                            ("instruction", "instruction"),
                            ("summary", "summary"),
                            ("other", "other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("content", models.TextField()),
                ("tags", models.JSONField(blank=True, default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "active"), ("superseded", "superseded"), ("archived", "archived")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "source_message",
                    models.ForeignKey(blank=True, null=True, on_delete=SET_NULL, related_name="memory_items", to="nova.message"),
                ),
                (
                    "source_thread",
                    models.ForeignKey(blank=True, null=True, on_delete=SET_NULL, related_name="memory_items", to="nova.thread"),
                ),
                (
                    "supersedes",
                    models.ForeignKey(blank=True, null=True, on_delete=SET_NULL, related_name="superseded_by", to="nova.memoryitem"),
                ),
                (
                    "theme",
                    models.ForeignKey(blank=True, null=True, on_delete=SET_NULL, related_name="items", to="nova.memorytheme"),
                ),
                (
                    "user",
                    models.ForeignKey(on_delete=CASCADE, related_name="memory_items", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "created_at"], name="idx_memory_item_user_created"),
                    models.Index(fields=["user", "theme", "created_at"], name="idx_mem_item_u_t_created"),
                    models.Index(fields=["user", "type"], name="idx_memory_item_user_type"),
                    models.Index(fields=["user", "status"], name="idx_memory_item_user_status"),
                ],
            },
        ),

        migrations.CreateModel(
            name="MemoryItemEmbedding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_type", models.CharField(blank=True, default="", max_length=40)),
                ("model", models.CharField(blank=True, default="", max_length=120)),
                ("dimensions", models.IntegerField(blank=True, null=True)),
                (
                    "state",
                    models.CharField(
                        choices=[("pending", "pending"), ("ready", "ready"), ("error", "error")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("error", models.TextField(blank=True, null=True)),
                (
                    "vector",
                    pgvector.django.vector.VectorField(blank=True, dimensions=1024, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "item",
                    models.OneToOneField(on_delete=CASCADE, related_name="embedding", to="nova.memoryitem"),
                ),
                (
                    "user",
                    models.ForeignKey(on_delete=CASCADE, related_name="memory_item_embeddings", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "state"], name="idx_memory_embed_user_state"),
                ],
            },
        ),
    ]
