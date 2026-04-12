import logging

from celery import shared_task
from django.db import transaction

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.memory_common import MemoryChunkEmbeddingState, MemoryRecordStatus
from nova.tasks.memory_tasks import compute_memory_chunk_embedding_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="rebuild_user_memory_embeddings")
def rebuild_user_memory_embeddings_task(self, user_id: int, batch_size: int = 500):
    """Mark all of a user's memory chunk embeddings as pending and enqueue recomputation."""

    active_chunks = MemoryChunk.objects.filter(
        document__user_id=user_id,
        document__status=MemoryRecordStatus.ACTIVE,
        status=MemoryRecordStatus.ACTIVE,
    )

    chunks_without_embeddings = active_chunks.exclude(
        id__in=MemoryChunkEmbedding.objects.values_list("chunk_id", flat=True)
    )

    with transaction.atomic():
        for chunk in chunks_without_embeddings:
            MemoryChunkEmbedding.objects.create(
                chunk=chunk,
                state=MemoryChunkEmbeddingState.PENDING,
            )

    qs = MemoryChunkEmbedding.objects.filter(
        chunk__document__user_id=user_id,
        chunk__document__status=MemoryRecordStatus.ACTIVE,
        chunk__status=MemoryRecordStatus.ACTIVE,
    )

    with transaction.atomic():
        qs.update(
            state=MemoryChunkEmbeddingState.PENDING,
            error=None,
            vector=None,
        )

    chunk_ids = list(qs.values_list("chunk_id", flat=True)[:batch_size])
    for chunk_id in chunk_ids:
        compute_memory_chunk_embedding_task.delay(chunk_id)

    remaining = qs.count() - len(chunk_ids)
    if remaining > 0:
        rebuild_user_memory_embeddings_task.delay(user_id, batch_size=batch_size)

    logger.info(
        "[rebuild_user_memory_embeddings] user=%s created=%s queued=%s remaining=%s",
        user_id,
        chunks_without_embeddings.count(),
        len(chunk_ids),
        max(remaining, 0),
    )
