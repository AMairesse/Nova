from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from django.db import transaction

from nova.llm.embeddings import compute_embedding, get_embeddings_provider
from nova.models.ConversationEmbedding import (
    ConversationEmbeddingState,
    DaySegmentEmbedding,
    TranscriptChunkEmbedding,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="compute_day_segment_embedding")
def compute_day_segment_embedding_task(self, embedding_id: int):
    emb = (
        DaySegmentEmbedding.objects.select_related("day_segment")
        .filter(id=embedding_id)
        .first()
    )
    if not emb:
        return

    provider = get_embeddings_provider(user_id=emb.user_id)
    if not provider:
        logger.info("[compute_day_segment_embedding] embeddings disabled; skipping %s", embedding_id)
        return

    summary_text = (emb.day_segment.summary_markdown or "").strip()
    if not summary_text:
        emb.state = ConversationEmbeddingState.ERROR
        emb.error = "empty_summary"
        emb.save(update_fields=["state", "error", "updated_at"])
        return

    try:
        vec = async_to_sync(compute_embedding)(summary_text, user_id=emb.user_id)
        if vec is None:
            emb.state = ConversationEmbeddingState.ERROR
            emb.error = "embeddings_disabled"
            emb.save(update_fields=["state", "error", "updated_at"])
            return

        emb.provider_type = provider.provider_type
        emb.model = provider.model
        emb.dimensions = len(vec)
        emb.vector = vec
        emb.state = ConversationEmbeddingState.READY
        emb.error = None
        emb.save(
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
    except Exception as e:
        logger.exception("[compute_day_segment_embedding] failed for %s", embedding_id)
        emb.state = ConversationEmbeddingState.ERROR
        emb.error = str(e)
        emb.save(update_fields=["state", "error", "updated_at"])
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="compute_transcript_chunk_embedding")
def compute_transcript_chunk_embedding_task(self, embedding_id: int):
    emb = (
        TranscriptChunkEmbedding.objects.select_related("transcript_chunk")
        .filter(id=embedding_id)
        .first()
    )
    if not emb:
        return

    provider = get_embeddings_provider(user_id=emb.user_id)
    if not provider:
        logger.info("[compute_transcript_chunk_embedding] embeddings disabled; skipping %s", embedding_id)
        return

    content_text = (emb.transcript_chunk.content_text or "").strip()
    if not content_text:
        emb.state = ConversationEmbeddingState.ERROR
        emb.error = "empty_content"
        emb.save(update_fields=["state", "error", "updated_at"])
        return

    try:
        vec = async_to_sync(compute_embedding)(content_text, user_id=emb.user_id)
        if vec is None:
            emb.state = ConversationEmbeddingState.ERROR
            emb.error = "embeddings_disabled"
            emb.save(update_fields=["state", "error", "updated_at"])
            return

        emb.provider_type = provider.provider_type
        emb.model = provider.model
        emb.dimensions = len(vec)
        emb.vector = vec
        emb.state = ConversationEmbeddingState.READY
        emb.error = None
        emb.save(
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
    except Exception as e:
        logger.exception("[compute_transcript_chunk_embedding] failed for %s", embedding_id)
        emb.state = ConversationEmbeddingState.ERROR
        emb.error = str(e)
        emb.save(update_fields=["state", "error", "updated_at"])
        raise self.retry(countdown=60, exc=e)


@shared_task(bind=True, name="rebuild_user_conversation_embeddings")
def rebuild_user_conversation_embeddings_task(self, user_id: int, batch_size: int = 500):
    # Ensure rows exist for all current conversation artifacts.
    from nova.models.DaySegment import DaySegment
    from nova.models.TranscriptChunk import TranscriptChunk

    with transaction.atomic():
        missing_day_segments = DaySegment.objects.filter(user_id=user_id).exclude(
            id__in=DaySegmentEmbedding.objects.filter(user_id=user_id).values_list("day_segment_id", flat=True)
        )
        for seg in missing_day_segments:
            DaySegmentEmbedding.objects.create(user_id=user_id, day_segment=seg)

        missing_chunks = TranscriptChunk.objects.filter(user_id=user_id).exclude(
            id__in=TranscriptChunkEmbedding.objects.filter(user_id=user_id).values_list(
                "transcript_chunk_id", flat=True
            )
        )
        for ch in missing_chunks:
            TranscriptChunkEmbedding.objects.create(user_id=user_id, transcript_chunk=ch)

    day_qs = DaySegmentEmbedding.objects.filter(user_id=user_id)
    chunk_qs = TranscriptChunkEmbedding.objects.filter(user_id=user_id)

    day_ids = list(day_qs.values_list("id", flat=True)[:batch_size])
    chunk_ids = list(chunk_qs.values_list("id", flat=True)[:batch_size])

    DaySegmentEmbedding.objects.filter(id__in=day_ids).update(
        state=ConversationEmbeddingState.PENDING,
        error=None,
        vector=None,
    )
    TranscriptChunkEmbedding.objects.filter(id__in=chunk_ids).update(
        state=ConversationEmbeddingState.PENDING,
        error=None,
        vector=None,
    )

    for emb_id in day_ids:
        compute_day_segment_embedding_task.delay(emb_id)
    for emb_id in chunk_ids:
        compute_transcript_chunk_embedding_task.delay(emb_id)

    if len(day_ids) == batch_size or len(chunk_ids) == batch_size:
        rebuild_user_conversation_embeddings_task.delay(user_id, batch_size=batch_size)

    logger.info(
        "[rebuild_user_conversation_embeddings] user=%s queued_day=%s queued_chunk=%s",
        user_id,
        len(day_ids),
        len(chunk_ids),
    )
    return {
        "status": "ok",
        "user_id": user_id,
        "queued_day": len(day_ids),
        "queued_chunk": len(chunk_ids),
    }
