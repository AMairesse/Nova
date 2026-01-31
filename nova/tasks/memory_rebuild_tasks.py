import logging

from celery import shared_task
from django.db import transaction

from nova.models.Memory import MemoryEmbeddingState, MemoryItem, MemoryItemEmbedding
from nova.tasks.memory_tasks import compute_memory_item_embedding_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="rebuild_user_memory_embeddings")
def rebuild_user_memory_embeddings_task(self, user_id: int, batch_size: int = 500):
    """Mark all of a user's embeddings as pending and enqueue recomputation.

    This is used when the embeddings provider/model changes.
    Also creates missing embeddings for memory items that don't have them.
    """

    # First, create missing embeddings for memory items without them
    memory_items_without_embeddings = MemoryItem.objects.filter(
        user_id=user_id
    ).exclude(
        id__in=MemoryItemEmbedding.objects.filter(user_id=user_id).values_list('item_id', flat=True)
    )

    with transaction.atomic():
        for item in memory_items_without_embeddings:
            MemoryItemEmbedding.objects.create(
                user_id=user_id,
                item=item,
                state=MemoryEmbeddingState.PENDING
            )

    qs = MemoryItemEmbedding.objects.filter(user_id=user_id)

    # Mark as pending (best-effort). We purposely do not delete rows.
    with transaction.atomic():
        qs.update(state=MemoryEmbeddingState.PENDING, error=None, vector=None)

    # Enqueue recomputation in batches.
    ids = list(qs.values_list("id", flat=True)[:batch_size])
    for emb_id in ids:
        compute_memory_item_embedding_task.delay(emb_id)

    remaining = qs.count() - len(ids)
    if remaining > 0:
        # Re-enqueue self to continue; avoids a single huge task.
        rebuild_user_memory_embeddings_task.delay(user_id, batch_size=batch_size)

    logger.info(
        "[rebuild_user_memory_embeddings] user=%s created=%s queued=%s remaining=%s",
        user_id,
        memory_items_without_embeddings.count(),
        len(ids),
        max(remaining, 0),
    )
