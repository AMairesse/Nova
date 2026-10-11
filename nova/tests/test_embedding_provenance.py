"""Integration checks: pgvector must never compare different embedding spaces."""
from unittest import skipUnless
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.db import connection
from django.test import TestCase, override_settings

from nova.llm.embeddings import EmbeddingsProvider, embedding_provider_fingerprint
from nova.llm.hybrid_search import compatible_embedding_distance
from nova.memory.service import search_memory_items
from nova.continuous.tools.conversation_tools import conversation_search
from nova.models.MemoryDocument import MemoryDocument
from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.ConversationEmbedding import DaySegmentEmbedding, TranscriptChunkEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.TranscriptChunk import TranscriptChunk
from nova.models.Thread import Thread
from nova.models.Message import Message, Actor
from nova.tests.factories import create_user


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL/pgvector")
@override_settings(MEMORY_EMBEDDINGS_URL=None)
class EmbeddingProvenanceTests(TestCase):
    def test_old_model_and_old_endpoint_are_excluded_but_sources_remain_searchable(self):
        user = create_user()
        provider = EmbeddingsProvider("custom_http", "http://local/v1", "new-model")
        providers = [EmbeddingsProvider("custom_http", "http://local/v1", "old-model"),
                     EmbeddingsProvider("custom_http", "http://other/v1", "new-model"), provider]
        vec = [1.0] + [0.0] * 1023
        thread = Thread.objects.create(user=user, subject="test", mode=Thread.Mode.CONTINUOUS)
        segment_ids, chunk_ids, memory_ids = [], [], []
        for i, stored_provider in enumerate(providers):
            message = Message.objects.create(user=user, thread=thread, actor=Actor.USER, text="source")
            segment = DaySegment.objects.create(user=user, thread=thread, day_label=f"2026-10-{i+1:02}",
                                               starts_at_message=message, summary_markdown="source")
            transcript = TranscriptChunk.objects.create(user=user, thread=thread, day_segment=segment,
                start_message=message, end_message=message, content_text="source", content_hash=str(i))
            document = MemoryDocument.objects.create(user=user, virtual_path=f"/memory/{i}.md",
                                                      title="test", content_markdown="source")
            memory = MemoryChunk.objects.create(document=document, heading="test", anchor="test", position=0,
                                                content_text="source", token_count=1)
            fields = dict(state="ready", vector=vec, provider_type=stored_provider.provider_type,
                          model=stored_provider.model, provider_fingerprint=embedding_provider_fingerprint(stored_provider))
            MemoryChunkEmbedding.objects.create(chunk=memory, **fields)
            DaySegmentEmbedding.objects.create(user=user, day_segment=segment, **fields)
            TranscriptChunkEmbedding.objects.create(user=user, transcript_chunk=transcript, **fields)
            segment_ids.append(segment.id)
            chunk_ids.append(transcript.id)
            memory_ids.append(memory.id)

        for model, ids in ((MemoryChunk, memory_ids), (DaySegment, segment_ids), (TranscriptChunk, chunk_ids)):
            distances = dict(model.objects.filter(id__in=ids).annotate(
                distance=compatible_embedding_distance(vec, provider)).values_list("id", "distance"))
            self.assertIsNone(distances[ids[0]])
            self.assertIsNone(distances[ids[1]])
            self.assertAlmostEqual(distances[ids[2]], 0.0)

        with patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=provider), patch(
            "nova.memory.service.resolve_query_vector", new_callable=AsyncMock, return_value=vec):
            semantic = async_to_sync(search_memory_items)(query="zzznomatchzzz", user=user)
            self.assertEqual([item["path"] for item in semantic["results"]], ["/memory/2.md"], semantic)
            lexical = async_to_sync(search_memory_items)(query="source", user=user)
            self.assertEqual(len(lexical["results"]), 3)
            for item in lexical["results"]:
                if item["path"] != "/memory/2.md":
                    self.assertFalse(item["signals"]["semantic"])

        with patch("nova.continuous.tools.conversation_tools.aget_embeddings_provider", new_callable=AsyncMock, return_value=provider), patch(
            "nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock, return_value=vec):
            semantic = async_to_sync(conversation_search)(query="zzznomatchzzz", agent=SimpleNamespace(user=user, thread=thread),
                                                          recency_days=10000)
            self.assertEqual(len(semantic["results"]), 2)
            self.assertEqual({item["day_segment_id"] for item in semantic["results"]}, {segment_ids[2]})
            lexical = async_to_sync(conversation_search)(query="source", agent=SimpleNamespace(user=user, thread=thread),
                                                         recency_days=10000, limit=10)
            self.assertEqual(len(lexical["results"]), 6)
