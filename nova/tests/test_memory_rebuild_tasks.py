from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.memory.service import write_memory_document
from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryChunkEmbeddingState, MemoryRecordStatus
from nova.tasks.memory_rebuild_tasks import rebuild_user_memory_embeddings_task


User = get_user_model()


class MemoryRebuildTasksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-rebuild-user",
            email="memory-rebuild@example.com",
            password="testpass123",
        )

    def _create_chunk(self, path: str, text: str) -> MemoryChunk:
        document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path=path,
            title="Memory",
            content_markdown=f"# Memory\n\n{text}",
            status=MemoryRecordStatus.ACTIVE,
        )
        return MemoryChunk.objects.create(
            document=document,
            heading="Memory",
            anchor="memory",
            position=0,
            content_text=text,
            token_count=len(text.split()),
            status=MemoryRecordStatus.ACTIVE,
        )

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.memory_rebuild_tasks.compute_memory_chunk_embedding_task.delay")
    def test_creates_missing_embeddings_marks_all_pending_and_queues_batch(
        self,
        mocked_compute_delay,
        mocked_rebuild_delay,
    ):
        existing_chunk = self._create_chunk("/memory/existing.md", "existing")
        missing_chunk = self._create_chunk("/memory/missing.md", "missing")
        existing_embedding = MemoryChunkEmbedding.objects.create(
            chunk=existing_chunk,
            state=MemoryChunkEmbeddingState.ERROR,
            error="old error",
            vector=[0.4] * 1024,
        )

        rebuild_user_memory_embeddings_task.run(self.user.id, batch_size=10)

        embeddings = list(
            MemoryChunkEmbedding.objects.filter(chunk__document__user=self.user).order_by(
                "chunk__document__virtual_path"
            )
        )
        self.assertEqual(len(embeddings), 2)
        self.assertTrue(
            MemoryChunkEmbedding.objects.filter(chunk=missing_chunk).exists()
        )

        existing_embedding.refresh_from_db()
        self.assertEqual(existing_embedding.state, MemoryChunkEmbeddingState.PENDING)
        self.assertIsNone(existing_embedding.error)
        self.assertIsNone(existing_embedding.vector)

        queued_ids = {call.args[0] for call in mocked_compute_delay.call_args_list}
        self.assertEqual(
            queued_ids,
            set(
                MemoryChunkEmbedding.objects.filter(chunk__document__user=self.user).values_list(
                    "chunk_id",
                    flat=True,
                )
            ),
        )
        mocked_rebuild_delay.assert_not_called()

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.memory_rebuild_tasks.compute_memory_chunk_embedding_task.delay")
    def test_requeues_itself_when_more_embeddings_remain_than_batch_size(
        self,
        mocked_compute_delay,
        mocked_rebuild_delay,
    ):
        for index in range(3):
            chunk = self._create_chunk(f"/memory/item-{index}.md", f"item-{index}")
            MemoryChunkEmbedding.objects.create(
                chunk=chunk,
                state=MemoryChunkEmbeddingState.READY,
                vector=[0.2] * 1024,
            )

        rebuild_user_memory_embeddings_task.run(self.user.id, batch_size=2)

        self.assertEqual(mocked_compute_delay.call_count, 2)
        mocked_rebuild_delay.assert_called_once_with(self.user.id, batch_size=2)
        self.assertEqual(
            MemoryChunkEmbedding.objects.filter(
                chunk__document__user=self.user,
                state=MemoryChunkEmbeddingState.PENDING,
                vector=None,
                error=None,
            ).count(),
            3,
        )

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.memory_rebuild_tasks.compute_memory_chunk_embedding_task.delay")
    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_rebuild_queues_pending_embeddings_created_while_provider_was_unavailable(
        self,
        mocked_provider,
        mocked_compute_delay,
        mocked_rebuild_delay,
    ):
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/offline.md",
            text="# Offline\n\n## Follow-up\nQueue later",
        )

        embeddings = list(
            MemoryChunkEmbedding.objects.filter(chunk__document__user=self.user).order_by("chunk_id")
        )
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0].state, MemoryChunkEmbeddingState.PENDING)
        mocked_compute_delay.assert_not_called()

        rebuild_user_memory_embeddings_task.run(self.user.id, batch_size=10)

        queued_ids = {call.args[0] for call in mocked_compute_delay.call_args_list}
        self.assertEqual(queued_ids, {embeddings[0].chunk_id})
        mocked_rebuild_delay.assert_not_called()
        mocked_provider.assert_awaited()
