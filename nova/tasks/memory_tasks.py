import logging

from celery import shared_task
from django.db import transaction

from nova.llm.embeddings import compute_embedding, get_embeddings_provider
from nova.models.Memory import MemoryEmbeddingState, MemoryItemEmbedding

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="compute_memory_item_embedding")
def compute_memory_item_embedding_task(self, embedding_id: int):
    """Compute pgvector embedding for a MemoryItemEmbedding.

    This is intentionally separate from the tool call to keep tool latency low.
    """

    from asgiref.sync import async_to_sync

    provider = get_embeddings_provider()
    if not provider:
        logger.info("[compute_memory_item_embedding] embeddings disabled; skipping %s", embedding_id)
        return

    emb = MemoryItemEmbedding.objects.select_related("item").filter(id=embedding_id).first()
    if not emb:
        return

    # idempotence
    if emb.state == MemoryEmbeddingState.READY and emb.vector is not None:
        return

    try:
        vec = async_to_sync(compute_embedding)(emb.item.content)
        if vec is None:
            return

        with transaction.atomic():
            emb.provider_type = provider.provider_type
            emb.model = provider.model
            emb.dimensions = len(vec)
            emb.vector = vec
            emb.state = MemoryEmbeddingState.READY
            emb.error = None
            emb.save(update_fields=[
                "provider_type",
                "model",
                "dimensions",
                "vector",
                "state",
                "error",
                "updated_at",
            ])
    except Exception as e:
        logger.exception("[compute_memory_item_embedding] failed for %s", embedding_id)
        emb.state = MemoryEmbeddingState.ERROR
        emb.error = str(e)
        emb.save(update_fields=["state", "error", "updated_at"])
