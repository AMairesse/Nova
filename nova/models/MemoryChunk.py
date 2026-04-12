from django.db import models

from .memory_common import MemoryRecordStatus


class MemoryChunk(models.Model):
    document = models.ForeignKey(
        "nova.MemoryDocument",
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    heading = models.CharField(max_length=255, blank=True, default="")
    anchor = models.CharField(max_length=255, blank=True, default="")
    position = models.IntegerField(default=0)
    content_text = models.TextField(blank=True, default="")
    token_count = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=MemoryRecordStatus.choices,
        default=MemoryRecordStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["document", "status", "position"], name="idx_mem_chunk_doc_pos"),
            models.Index(fields=["status"], name="idx_mem_chunk_status"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}:{self.position}"
