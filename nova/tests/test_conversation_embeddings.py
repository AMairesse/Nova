from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.ConversationEmbedding import (
    ConversationEmbeddingState,
    DaySegmentEmbedding,
    TranscriptChunkEmbedding,
)
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk
from nova.tasks.conversation_embedding_tasks import (
    compute_day_segment_embedding_task,
    compute_transcript_chunk_embedding_task,
    rebuild_user_conversation_embeddings_task,
)
from nova.continuous.tools.conversation_tools import _focused_snippet, conversation_get, conversation_search


User = get_user_model()


class ConversationEmbeddingTasksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="conv-user", email="conv@example.com", password="testpass123")
        self.thread = Thread.objects.create(user=self.user, subject="Continuous", mode=Thread.Mode.CONTINUOUS)

        self.msg1 = Message.objects.create(user=self.user, thread=self.thread, actor=Actor.USER, text="hello")
        self.msg2 = Message.objects.create(user=self.user, thread=self.thread, actor=Actor.AGENT, text="world")

        self.seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=self.msg1.created_at.date(),
            starts_at_message=self.msg1,
            summary_markdown="Summary about deployment and retries",
        )
        self.chunk = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=self.seg,
            start_message=self.msg1,
            end_message=self.msg2,
            content_text="User: hello\nAgent: world",
            content_hash=TranscriptChunk.compute_hash("User: hello\nAgent: world", self.msg1.id, self.msg2.id),
            token_estimate=8,
        )

    def _create_additional_segment_and_chunk(self, suffix: str):
        start_message = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.USER,
            text=f"hello {suffix}",
        )
        end_message = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.AGENT,
            text=f"world {suffix}",
        )
        segment = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=self.seg.day_label + timedelta(days=1),
            starts_at_message=start_message,
            summary_markdown=f"Summary {suffix}",
        )
        chunk = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=segment,
            start_message=start_message,
            end_message=end_message,
            content_text=f"User: hello {suffix}\nAgent: world {suffix}",
            content_hash=TranscriptChunk.compute_hash(
                f"User: hello {suffix}\nAgent: world {suffix}",
                start_message.id,
                end_message.id,
            ),
            token_estimate=12,
        )
        return segment, chunk

    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_returns_when_embedding_is_missing(self, mock_provider):
        compute_day_segment_embedding_task.run(999999)

        mock_provider.assert_not_called()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_skips_when_provider_is_disabled(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = DaySegmentEmbedding.objects.create(user=self.user, day_segment=self.seg)
        mock_provider.return_value = None

        compute_day_segment_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.PENDING)
        mock_compute.assert_not_awaited()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_marks_error_for_empty_summary(
        self,
        mock_provider,
        mock_compute,
    ):
        self.seg.summary_markdown = "   "
        self.seg.save(update_fields=["summary_markdown"])
        emb = DaySegmentEmbedding.objects.create(user=self.user, day_segment=self.seg)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")

        compute_day_segment_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "empty_summary")
        mock_compute.assert_not_awaited()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_marks_ready(self, mock_provider, mock_compute):
        emb = DaySegmentEmbedding.objects.create(user=self.user, day_segment=self.seg)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.return_value = [0.1] * 1024

        compute_day_segment_embedding_task(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.READY)
        self.assertEqual(emb.model, "e5")
        self.assertEqual(emb.dimensions, 1024)
        self.assertIsNotNone(emb.vector)

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_marks_error_when_provider_returns_no_vector(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = DaySegmentEmbedding.objects.create(user=self.user, day_segment=self.seg)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.return_value = None

        compute_day_segment_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "embeddings_disabled")

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_day_segment_embedding_retries_after_failure(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = DaySegmentEmbedding.objects.create(user=self.user, day_segment=self.seg)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.side_effect = RuntimeError("boom")

        with patch.object(
            compute_day_segment_embedding_task,
            "retry",
            side_effect=RuntimeError("retry scheduled"),
        ) as mocked_retry, self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            compute_day_segment_embedding_task(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "boom")
        mocked_retry.assert_called_once()

    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_returns_when_embedding_is_missing(self, mock_provider):
        compute_transcript_chunk_embedding_task.run(999999)

        mock_provider.assert_not_called()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_skips_when_provider_is_disabled(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = TranscriptChunkEmbedding.objects.create(user=self.user, transcript_chunk=self.chunk)
        mock_provider.return_value = None

        compute_transcript_chunk_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.PENDING)
        mock_compute.assert_not_awaited()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_marks_error_for_empty_content(
        self,
        mock_provider,
        mock_compute,
    ):
        self.chunk.content_text = "   "
        self.chunk.save(update_fields=["content_text"])
        emb = TranscriptChunkEmbedding.objects.create(user=self.user, transcript_chunk=self.chunk)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")

        compute_transcript_chunk_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "empty_content")
        mock_compute.assert_not_awaited()

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_marks_ready(self, mock_provider, mock_compute):
        emb = TranscriptChunkEmbedding.objects.create(user=self.user, transcript_chunk=self.chunk)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.return_value = [0.2] * 1024

        compute_transcript_chunk_embedding_task(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.READY)
        self.assertEqual(emb.model, "e5")
        self.assertEqual(emb.dimensions, 1024)
        self.assertIsNotNone(emb.vector)

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_marks_error_when_provider_returns_no_vector(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = TranscriptChunkEmbedding.objects.create(user=self.user, transcript_chunk=self.chunk)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.return_value = None

        compute_transcript_chunk_embedding_task.run(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "embeddings_disabled")

    @patch("nova.tasks.conversation_embedding_tasks.compute_embedding", new_callable=AsyncMock)
    @patch("nova.tasks.conversation_embedding_tasks.get_embeddings_provider")
    def test_compute_transcript_chunk_embedding_retries_after_failure(
        self,
        mock_provider,
        mock_compute,
    ):
        emb = TranscriptChunkEmbedding.objects.create(user=self.user, transcript_chunk=self.chunk)
        mock_provider.return_value = SimpleNamespace(provider_type="custom_http", model="e5")
        mock_compute.side_effect = RuntimeError("boom")

        with patch.object(
            compute_transcript_chunk_embedding_task,
            "retry",
            side_effect=RuntimeError("retry scheduled"),
        ) as mocked_retry, self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            compute_transcript_chunk_embedding_task(emb.id)

        emb.refresh_from_db()
        self.assertEqual(emb.state, ConversationEmbeddingState.ERROR)
        self.assertEqual(emb.error, "boom")
        mocked_retry.assert_called_once()

    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.compute_transcript_chunk_embedding_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.compute_day_segment_embedding_task.delay")
    def test_rebuild_user_conversation_embeddings_creates_rows_and_queues_pending_batches(
        self,
        mock_day_delay,
        mock_chunk_delay,
        mock_rebuild_delay,
    ):
        result = rebuild_user_conversation_embeddings_task.run(self.user.id, batch_size=10)

        day_embedding = DaySegmentEmbedding.objects.get(day_segment=self.seg)
        chunk_embedding = TranscriptChunkEmbedding.objects.get(transcript_chunk=self.chunk)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["queued_day"], 1)
        self.assertEqual(result["queued_chunk"], 1)
        self.assertEqual(day_embedding.state, ConversationEmbeddingState.PENDING)
        self.assertIsNone(day_embedding.error)
        self.assertIsNone(day_embedding.vector)
        self.assertEqual(chunk_embedding.state, ConversationEmbeddingState.PENDING)
        self.assertIsNone(chunk_embedding.error)
        self.assertIsNone(chunk_embedding.vector)
        mock_day_delay.assert_called_once_with(day_embedding.id)
        mock_chunk_delay.assert_called_once_with(chunk_embedding.id)
        mock_rebuild_delay.assert_not_called()

    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.compute_transcript_chunk_embedding_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.compute_day_segment_embedding_task.delay")
    def test_rebuild_user_conversation_embeddings_reschedules_when_batch_is_full(
        self,
        mock_day_delay,
        mock_chunk_delay,
        mock_rebuild_delay,
    ):
        second_segment, second_chunk = self._create_additional_segment_and_chunk("second")
        first_day_embedding = DaySegmentEmbedding.objects.create(
            user=self.user,
            day_segment=self.seg,
            state=ConversationEmbeddingState.ERROR,
            error="stale",
            vector=[0.1] * 4,
        )
        first_chunk_embedding = TranscriptChunkEmbedding.objects.create(
            user=self.user,
            transcript_chunk=self.chunk,
            state=ConversationEmbeddingState.ERROR,
            error="stale",
            vector=[0.2] * 4,
        )

        result = rebuild_user_conversation_embeddings_task.run(self.user.id, batch_size=1)

        first_day_embedding.refresh_from_db()
        first_chunk_embedding.refresh_from_db()
        self.assertEqual(result["queued_day"], 1)
        self.assertEqual(result["queued_chunk"], 1)
        self.assertEqual(first_day_embedding.state, ConversationEmbeddingState.PENDING)
        self.assertIsNone(first_day_embedding.error)
        self.assertIsNone(first_day_embedding.vector)
        self.assertEqual(first_chunk_embedding.state, ConversationEmbeddingState.PENDING)
        self.assertIsNone(first_chunk_embedding.error)
        self.assertIsNone(first_chunk_embedding.vector)
        self.assertTrue(DaySegmentEmbedding.objects.filter(day_segment=second_segment).exists())
        self.assertTrue(TranscriptChunkEmbedding.objects.filter(transcript_chunk=second_chunk).exists())
        mock_day_delay.assert_called_once_with(first_day_embedding.id)
        mock_chunk_delay.assert_called_once_with(first_chunk_embedding.id)
        mock_rebuild_delay.assert_called_once_with(self.user.id, batch_size=1)


class ConversationSearchFallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="search-user", email="search@example.com", password="testpass123")
        self.thread = Thread.objects.create(user=self.user, subject="Continuous", mode=Thread.Mode.CONTINUOUS)

        msg1 = Message.objects.create(user=self.user, thread=self.thread, actor=Actor.USER, text="how to deploy")
        msg2 = Message.objects.create(user=self.user, thread=self.thread, actor=Actor.AGENT, text="use blue green")

        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=msg1.created_at.date(),
            starts_at_message=msg1,
            summary_markdown="Deployment summary for production rollout",
        )
        TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=seg,
            start_message=msg1,
            end_message=msg2,
            content_text="User: how to deploy\nAgent: use blue green",
            content_hash=TranscriptChunk.compute_hash("User: how to deploy\nAgent: use blue green", msg1.id, msg2.id),
            token_estimate=12,
        )
        self.agent = SimpleNamespace(user=self.user, thread=self.thread)

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_returns_results_with_embeddings_disabled(self, mock_resolve_query_vec):
        mock_resolve_query_vec.return_value = None

        out = async_to_sync(conversation_search)(query="deploy", agent=self.agent, limit=10)

        self.assertIn("results", out)
        self.assertGreaterEqual(len(out["results"]), 1)

    def test_conversation_get_accepts_range_without_message_or_day_segment(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            from_message_id=1,
            to_message_id=999999,
            limit=3,
        )

        self.assertIn("messages", out)
        self.assertLessEqual(len(out["messages"]), 3)
        self.assertNotIn("error", out)


class ConversationSnippetFocusTests(TestCase):
    def test_focused_snippet_uses_headline_when_present(self):
        text = "Long introduction that is not relevant."
        headline = "... section with <mark>deploy</mark> strategy ..."

        snippet = _focused_snippet(text=text, query="deploy", headline=headline, max_len=240)

        self.assertIn("<mark>deploy</mark>", snippet)
        self.assertEqual(snippet, headline)

    def test_focused_snippet_fallback_centers_near_query_match(self):
        text = (
            "Prefix not relevant. " * 30
            + "This sentence explains blue green deploy strategy with rollback. "
            + "Suffix not relevant. " * 30
        )

        snippet = _focused_snippet(text=text, query="blue green deploy", headline=None, max_len=180)

        self.assertIn("deploy", snippet.lower())
        self.assertTrue(snippet.startswith("…") or snippet.endswith("…"))
