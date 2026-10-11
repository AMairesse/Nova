import logging

from celery import shared_task
from django.db import transaction

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.memory_common import MemoryChunkEmbeddingState, MemoryRecordStatus
from nova.tasks.memory_tasks import compute_memory_chunk_embedding_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="rebuild_user_memory_embeddings")
def rebuild_user_memory_embeddings_task(self, user_id: int, batch_size: int = 500, after_chunk_id: int = 0):
    """Invalidate once, then enqueue ordered batches without waiting for computation."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    active_chunks = MemoryChunk.objects.filter(
        document__user_id=user_id, document__status=MemoryRecordStatus.ACTIVE,
        status=MemoryRecordStatus.ACTIVE,
    )
    qs = MemoryChunkEmbedding.objects.filter(chunk__in=active_chunks)
    if after_chunk_id == 0:
        with transaction.atomic():
            for chunk in active_chunks.filter(embedding__isnull=True):
                MemoryChunkEmbedding.objects.get_or_create(chunk=chunk)
            qs.update(state=MemoryChunkEmbeddingState.PENDING, error=None, vector=None,
                      provider_fingerprint="")

    ids = list(qs.filter(chunk_id__gt=after_chunk_id).order_by("chunk_id")
               .values_list("chunk_id", flat=True)[:batch_size + 1])
    chunk_ids = ids[:batch_size]
    for chunk_id in chunk_ids:
        compute_memory_chunk_embedding_task.delay(chunk_id)
    if len(ids) > batch_size:
        rebuild_user_memory_embeddings_task.delay(
            user_id, batch_size=batch_size, after_chunk_id=chunk_ids[-1],
        )
    logger.info("[rebuild_user_memory_embeddings] user=%s after=%s queued=%s has_more=%s",
                user_id, after_chunk_id, len(chunk_ids), len(ids) > batch_size)
    return {"queued": len(chunk_ids), "has_more": len(ids) > batch_size}
