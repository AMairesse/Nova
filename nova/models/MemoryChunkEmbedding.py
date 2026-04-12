from django.db import models

from pgvector.django import VectorField

from .memory_common import MEMORY_EMBEDDING_DIMENSIONS, MemoryChunkEmbeddingState


class MemoryChunkEmbedding(models.Model):
    chunk = models.OneToOneField(
        "nova.MemoryChunk",
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    provider_type = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    dimensions = models.IntegerField(null=True, blank=True)
    state = models.CharField(
        max_length=20,
        choices=MemoryChunkEmbeddingState.choices,
        default=MemoryChunkEmbeddingState.PENDING,
    )
    error = models.TextField(null=True, blank=True)
    vector = VectorField(dimensions=MEMORY_EMBEDDING_DIMENSIONS, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state"], name="idx_mem_chunk_emb_state"),
        ]

    def __str__(self) -> str:
        return f"{self.chunk_id}:{self.state}"
