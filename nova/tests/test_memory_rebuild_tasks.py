from unittest.mock import patch

from django.test import TestCase

from nova.models.Memory import MemoryEmbeddingState, MemoryItem, MemoryItemEmbedding
from nova.tasks.memory_rebuild_tasks import rebuild_user_memory_embeddings_task
from nova.tests.factories import create_user


class MemoryRebuildTasksTests(TestCase):
    def setUp(self):
        self.user = create_user(
            username="memory-rebuild-user",
            email="memory-rebuild@example.com",
        )

    def _create_item(self, content):
        return MemoryItem.objects.create(
            user=self.user,
            type="fact",
            content=content,
        )

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.memory_rebuild_tasks.compute_memory_item_embedding_task.delay")
    def test_creates_missing_embeddings_marks_all_pending_and_queues_batch(
        self,
        mocked_compute_delay,
        mocked_rebuild_delay,
    ):
        existing_item = self._create_item("existing")
        missing_item = self._create_item("missing")
        existing_embedding = MemoryItemEmbedding.objects.create(
            user=self.user,
            item=existing_item,
            state=MemoryEmbeddingState.ERROR,
            error="old error",
            vector=[0.4] * 1024,
        )

        rebuild_user_memory_embeddings_task.run(self.user.id, batch_size=10)

        embeddings = list(
            MemoryItemEmbedding.objects.filter(user=self.user).order_by("item__content")
        )
        self.assertEqual(len(embeddings), 2)
        self.assertTrue(
            MemoryItemEmbedding.objects.filter(user=self.user, item=missing_item).exists()
        )

        existing_embedding.refresh_from_db()
        self.assertEqual(existing_embedding.state, MemoryEmbeddingState.PENDING)
        self.assertIsNone(existing_embedding.error)
        self.assertIsNone(existing_embedding.vector)

        queued_ids = {
            call.args[0]
            for call in mocked_compute_delay.call_args_list
        }
        self.assertEqual(
            queued_ids,
            set(MemoryItemEmbedding.objects.filter(user=self.user).values_list("id", flat=True)),
        )
        mocked_rebuild_delay.assert_not_called()

    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.memory_rebuild_tasks.compute_memory_item_embedding_task.delay")
    def test_requeues_itself_when_more_embeddings_remain_than_batch_size(
        self,
        mocked_compute_delay,
        mocked_rebuild_delay,
    ):
        for index in range(3):
            item = self._create_item(f"item-{index}")
            MemoryItemEmbedding.objects.create(
                user=self.user,
                item=item,
                state=MemoryEmbeddingState.READY,
                vector=[0.2] * 1024,
            )

        rebuild_user_memory_embeddings_task.run(self.user.id, batch_size=2)

        self.assertEqual(mocked_compute_delay.call_count, 2)
        mocked_rebuild_delay.assert_called_once_with(self.user.id, batch_size=2)
        self.assertEqual(
            MemoryItemEmbedding.objects.filter(
                user=self.user,
                state=MemoryEmbeddingState.PENDING,
                vector=None,
                error=None,
            ).count(),
            3,
        )
