from __future__ import annotations

import re

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from django.utils.text import slugify
import django.db.models.deletion
import pgvector.django.vector


README_PATH = "/memory/README.md"
MEMORY_ROOT = "/memory"
EMBED_DIMENSIONS = 1024


def _extract_title(text: str, fallback: str) -> str:
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return fallback
    for line in source.split("\n"):
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            candidate = candidate.lstrip("#").strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if candidate:
            return candidate[:120]
    first_sentence = re.split(r"[.!?\n]", source, maxsplit=1)[0].strip()
    if first_sentence:
        return re.sub(r"\s+", " ", first_sentence)[:120]
    return fallback


def _slug_from_title(title: str, fallback: str) -> str:
    slug = slugify(title or "")
    return slug or fallback


def _humanize_slug(value: str) -> str:
    return str(value or "").replace("-", " ").replace("_", " ").strip().title() or "Memory"


def backfill_memory_documents(apps, schema_editor):
    LegacyMemoryItem = apps.get_model("nova", "MemoryItem")
    MemoryDocument = apps.get_model("nova", "MemoryDocument")
    MemoryChunk = apps.get_model("nova", "MemoryChunk")
    MemoryChunkEmbedding = apps.get_model("nova", "MemoryChunkEmbedding")

    active_items = list(
        LegacyMemoryItem.objects.select_related("theme")
        .filter(status="active")
        .order_by("user_id", "created_at", "id")
    )
    if not active_items:
        return

    grouped: dict[tuple[int, str], dict] = {}
    used_paths: dict[int, set[str]] = {}

    def allocate_path(user_id: int, base_slug: str) -> str:
        taken = used_paths.setdefault(int(user_id), set())
        candidate = f"{MEMORY_ROOT}/{base_slug}.md"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        suffix = 2
        while True:
            candidate = f"{MEMORY_ROOT}/{base_slug}-{suffix}.md"
            if candidate not in taken:
                taken.add(candidate)
                return candidate
            suffix += 1

    for item in active_items:
        theme_slug = str(getattr(getattr(item, "theme", None), "slug", "") or "").strip()
        if theme_slug and theme_slug.lower() != "general":
            path = f"{MEMORY_ROOT}/{slugify(theme_slug) or 'memory'}.md"
            used_paths.setdefault(int(item.user_id), set()).add(path)
            group_key = (int(item.user_id), path)
        else:
            section_title = _extract_title(item.content, fallback=f"Memory {item.id}")
            base_slug = _slug_from_title(section_title, fallback=f"memory-{item.id}")
            path = allocate_path(int(item.user_id), base_slug)
            group_key = (int(item.user_id), path)

        payload = grouped.setdefault(
            group_key,
            {
                "user_id": int(item.user_id),
                "path": path,
                "title": _humanize_slug(path.rsplit("/", 1)[-1].rsplit(".", 1)[0]),
                "source_thread_id": getattr(item, "source_thread_id", None),
                "source_message_id": getattr(item, "source_message_id", None),
                "sections": [],
                "created_at": getattr(item, "created_at", None),
            },
        )

        section_title = _extract_title(item.content, fallback=f"Memory {item.id}")
        section_body = str(item.content or "").strip()
        legacy_label = str(getattr(item, "type", "") or "").strip()
        prefix = f"_Legacy memory item {item.id}"
        if legacy_label:
            prefix += f" ({legacy_label})"
        prefix += "_"
        rendered_section = f"## {section_title}\n\n{prefix}\n\n{section_body}\n"
        payload["sections"].append(
            {
                "heading": section_title,
                "anchor": _slug_from_title(section_title, fallback=f"section-{item.id}"),
                "content_text": section_body,
                "rendered_markdown": rendered_section,
                "position": len(payload["sections"]),
            }
        )

    for payload in grouped.values():
        markdown = f"# {payload['title']}\n\n" + "\n".join(
            section["rendered_markdown"] for section in payload["sections"]
        ).strip() + "\n"
        document = MemoryDocument.objects.create(
            user_id=payload["user_id"],
            virtual_path=payload["path"],
            title=payload["title"],
            content_markdown=markdown,
            source_thread_id=payload["source_thread_id"],
            source_message_id=payload["source_message_id"],
            status="active",
        )
        if payload["created_at"] is not None:
            MemoryDocument.objects.filter(id=document.id).update(created_at=payload["created_at"])

        for section in payload["sections"]:
            chunk = MemoryChunk.objects.create(
                document_id=document.id,
                heading=section["heading"],
                anchor=section["anchor"],
                position=section["position"],
                content_text=section["content_text"],
                token_count=len(str(section["content_text"] or "").split()),
                status="active",
            )
            MemoryChunkEmbedding.objects.create(
                chunk_id=chunk.id,
                state="pending",
                dimensions=EMBED_DIMENSIONS,
            )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nova", "0065_memoryitem_virtual_path"),
    ]

    operations = [
        migrations.CreateModel(
            name="MemoryDirectory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("virtual_path", models.CharField(max_length=512)),
                ("status", models.CharField(choices=[("active", "active"), ("archived", "archived")], default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memory_directories", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MemoryDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("virtual_path", models.CharField(max_length=512)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("content_markdown", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("active", "active"), ("archived", "archived")], default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="memory_documents", to="nova.message")),
                ("source_thread", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="memory_documents", to="nova.thread")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memory_documents", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MemoryChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("heading", models.CharField(blank=True, default="", max_length=255)),
                ("anchor", models.CharField(blank=True, default="", max_length=255)),
                ("position", models.IntegerField(default=0)),
                ("content_text", models.TextField(blank=True, default="")),
                ("token_count", models.IntegerField(default=0)),
                ("status", models.CharField(choices=[("active", "active"), ("archived", "archived")], default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chunks", to="nova.memorydocument")),
            ],
        ),
        migrations.CreateModel(
            name="MemoryChunkEmbedding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_type", models.CharField(blank=True, default="", max_length=40)),
                ("model", models.CharField(blank=True, default="", max_length=120)),
                ("dimensions", models.IntegerField(blank=True, null=True)),
                ("state", models.CharField(choices=[("pending", "pending"), ("ready", "ready"), ("error", "error")], default="pending", max_length=20)),
                ("error", models.TextField(blank=True, null=True)),
                ("vector", pgvector.django.vector.VectorField(blank=True, dimensions=EMBED_DIMENSIONS, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chunk", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="embedding", to="nova.memorychunk")),
            ],
        ),
        migrations.AddIndex(
            model_name="memorydirectory",
            index=models.Index(fields=["user", "virtual_path"], name="idx_mem_dir_u_path"),
        ),
        migrations.AddIndex(
            model_name="memorydirectory",
            index=models.Index(fields=["user", "status"], name="idx_mem_dir_u_status"),
        ),
        migrations.AddConstraint(
            model_name="memorydirectory",
            constraint=models.UniqueConstraint(condition=Q(status="active"), fields=("user", "virtual_path"), name="uniq_mem_dir_u_path_a"),
        ),
        migrations.AddIndex(
            model_name="memorydocument",
            index=models.Index(fields=["user", "virtual_path"], name="idx_mem_doc_u_path"),
        ),
        migrations.AddIndex(
            model_name="memorydocument",
            index=models.Index(fields=["user", "status"], name="idx_mem_doc_u_status"),
        ),
        migrations.AddIndex(
            model_name="memorydocument",
            index=models.Index(fields=["user", "updated_at"], name="idx_mem_doc_u_updated"),
        ),
        migrations.AddConstraint(
            model_name="memorydocument",
            constraint=models.UniqueConstraint(condition=Q(status="active"), fields=("user", "virtual_path"), name="uniq_mem_doc_u_path_a"),
        ),
        migrations.AddIndex(
            model_name="memorychunk",
            index=models.Index(fields=["document", "status", "position"], name="idx_mem_chunk_doc_pos"),
        ),
        migrations.AddIndex(
            model_name="memorychunk",
            index=models.Index(fields=["status"], name="idx_mem_chunk_status"),
        ),
        migrations.AddIndex(
            model_name="memorychunkembedding",
            index=models.Index(fields=["state"], name="idx_mem_chunk_emb_state"),
        ),
        migrations.RunPython(backfill_memory_documents, migrations.RunPython.noop),
    ]
