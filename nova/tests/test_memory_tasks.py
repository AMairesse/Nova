from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryChunkEmbeddingState, MemoryRecordStatus
from nova.tasks.memory_tasks import compute_memory_chunk_embedding_task


User = get_user_model()


class MemoryTasksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-task-user",
            email="memory-task@example.com",
            password="testpass123",
        )

    def _create_embedding(self, **kwargs):
        document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path="/memory/notes.md",
            title="Notes",
            content_markdown="# Notes\n\nRemember the Paris office opens at 9am.",
            status=MemoryRecordStatus.ACTIVE,
        )
        chunk = MemoryChunk.objects.create(
            document=document,
            heading="Notes",
            anchor="notes",
            position=0,
            content_text="Remember the Paris office opens at 9am.",
            token_count=8,
            status=MemoryRecordStatus.ACTIVE,
        )
        defaults = {
            "chunk": chunk,
            "state": MemoryChunkEmbeddingState.PENDING,
        }
        defaults.update(kwargs)
        return MemoryChunkEmbedding.objects.create(**defaults)

    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_returns_without_work_when_embedding_is_missing(self, mocked_get_provider):
        compute_memory_chunk_embedding_task.run(999999)

        mocked_get_provider.assert_not_called()

    @patch("nova.tasks.memory_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_returns_when_embeddings_provider_is_disabled(
        self,
        mocked_get_provider,
        mocked_compute_embedding,
    ):
        embedding = self._create_embedding()
        mocked_get_provider.return_value = None

        compute_memory_chunk_embedding_task.run(embedding.chunk_id)

        embedding.refresh_from_db()
        self.assertEqual(embedding.state, MemoryChunkEmbeddingState.PENDING)
        mocked_compute_embedding.assert_not_awaited()

    @patch("nova.tasks.memory_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_returns_when_embedding_is_already_ready(
        self,
        mocked_get_provider,
        mocked_compute_embedding,
    ):
        embedding = self._create_embedding(
            state=MemoryChunkEmbeddingState.READY,
            vector=[0.25] * 1024,
        )
        mocked_get_provider.return_value = SimpleNamespace(
            provider_type="custom_http",
            model="embed-small",
        )

        compute_memory_chunk_embedding_task.run(embedding.chunk_id)

        mocked_compute_embedding.assert_not_awaited()

    @patch("nova.tasks.memory_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_returns_when_embedding_computation_yields_none(
        self,
        mocked_get_provider,
        mocked_compute_embedding,
    ):
        embedding = self._create_embedding()
        mocked_get_provider.return_value = SimpleNamespace(
            provider_type="custom_http",
            model="embed-small",
        )
        mocked_compute_embedding.return_value = None

        compute_memory_chunk_embedding_task.run(embedding.chunk_id)

        embedding.refresh_from_db()
        self.assertEqual(embedding.state, MemoryChunkEmbeddingState.PENDING)
        self.assertIsNone(embedding.vector)

    @patch("nova.tasks.memory_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_stores_embedding_metadata_and_vector_on_success(
        self,
        mocked_get_provider,
        mocked_compute_embedding,
    ):
        embedding = self._create_embedding()
        mocked_get_provider.return_value = SimpleNamespace(
            provider_type="custom_http",
            model="embed-small",
        )
        mocked_compute_embedding.return_value = [0.5] * 1024

        compute_memory_chunk_embedding_task.run(embedding.chunk_id)

        embedding.refresh_from_db()
        self.assertEqual(embedding.provider_type, "custom_http")
        self.assertEqual(embedding.model, "embed-small")
        self.assertEqual(embedding.dimensions, 1024)
        self.assertEqual(embedding.state, MemoryChunkEmbeddingState.READY)
        self.assertIsNone(embedding.error)
        self.assertEqual(len(embedding.vector), 1024)

    @patch("nova.tasks.memory_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.memory_tasks.get_embeddings_provider")
    def test_marks_embedding_as_error_when_computation_fails(
        self,
        mocked_get_provider,
        mocked_compute_embedding,
    ):
        embedding = self._create_embedding()
        mocked_get_provider.return_value = SimpleNamespace(
            provider_type="custom_http",
            model="embed-small",
        )
        mocked_compute_embedding.side_effect = RuntimeError("boom")

        compute_memory_chunk_embedding_task.run(embedding.chunk_id)

        embedding.refresh_from_db()
        self.assertEqual(embedding.state, MemoryChunkEmbeddingState.ERROR)
        self.assertEqual(embedding.error, "boom")
