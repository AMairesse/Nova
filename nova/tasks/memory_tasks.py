import logging

from celery import shared_task
from django.db import transaction

from nova.llm.embeddings import compute_embedding, get_embeddings_provider
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.memory_common import MemoryChunkEmbeddingState

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="compute_memory_chunk_embedding")
def compute_memory_chunk_embedding_task(self, chunk_id: int):
    """Compute pgvector embedding for a MemoryChunkEmbedding."""

    from asgiref.sync import async_to_sync

    embedding = (
        MemoryChunkEmbedding.objects.select_related("chunk__document__user")
        .filter(chunk_id=chunk_id)
        .first()
    )
    if not embedding:
        return

    provider = get_embeddings_provider(user_id=embedding.chunk.document.user_id)
    if not provider:
        logger.info("[compute_memory_chunk_embedding] embeddings disabled; skipping %s", chunk_id)
        return

    if embedding.state == MemoryChunkEmbeddingState.READY and embedding.vector is not None:
        return

    try:
        vec = async_to_sync(compute_embedding)(
            embedding.chunk.content_text,
            user_id=embedding.chunk.document.user_id,
        )
        if vec is None:
            return

        with transaction.atomic():
            embedding.provider_type = provider.provider_type
            embedding.model = provider.model
            embedding.dimensions = len(vec)
            embedding.vector = vec
            embedding.state = MemoryChunkEmbeddingState.READY
            embedding.error = None
            embedding.save(
                update_fields=[
                    "provider_type",
                    "model",
                    "dimensions",
                    "vector",
                    "state",
                    "error",
                    "updated_at",
                ]
            )
    except Exception as exc:
        logger.exception("[compute_memory_chunk_embedding] failed for %s", chunk_id)
        embedding.state = MemoryChunkEmbeddingState.ERROR
        embedding.error = str(exc)
        embedding.save(update_fields=["state", "error", "updated_at"])
