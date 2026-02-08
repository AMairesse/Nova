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
