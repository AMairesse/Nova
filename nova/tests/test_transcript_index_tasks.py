from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from nova.models.ConversationEmbedding import TranscriptChunkEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk
from nova.tasks.transcript_index_tasks import (
    _index_transcript_append,
    _normalize_message_text,
)
from nova.tests.factories import create_user


class TranscriptIndexTasksTests(TestCase):
    def setUp(self):
        self.user = create_user(username="idx-user", email="idx@example.com")
        self.thread = Thread.objects.create(
            user=self.user,
            subject="continuous",
            mode=Thread.Mode.CONTINUOUS,
        )

    def test_normalize_message_text_filters_and_truncates(self):
        system_msg = SimpleNamespace(actor=Actor.SYSTEM, text="hidden")
        self.assertEqual(_normalize_message_text(system_msg), "")

        long_text = "x" * 4105
        user_msg = SimpleNamespace(actor=Actor.USER, text=long_text)
        normalized = _normalize_message_text(user_msg)

        self.assertTrue(normalized.startswith("User: "))
        self.assertIn("(truncated)", normalized)

    def test_index_transcript_append_returns_not_found(self):
        result = _index_transcript_append(day_segment_id=999999)
        self.assertEqual(result["status"], "not_found")

    @patch("nova.tasks.transcript_index_tasks.compute_transcript_chunk_embedding_task.delay")
    def test_index_transcript_append_creates_chunk_and_embedding(self, mocked_delay):
        m1 = self.thread.add_message("Hello", actor=Actor.USER)
        m2 = self.thread.add_message("Hi there", actor=Actor.AGENT)
        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=m1.created_at.date(),
            starts_at_message=m1,
        )

        result = _index_transcript_append(day_segment_id=seg.id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["created"], 1)
        chunk = TranscriptChunk.objects.get(day_segment=seg)
        self.assertEqual(chunk.start_message_id, m1.id)
        self.assertEqual(chunk.end_message_id, m2.id)

        emb = TranscriptChunkEmbedding.objects.get(transcript_chunk=chunk)
        self.assertEqual(emb.state, "pending")
        mocked_delay.assert_called_once_with(emb.id)

    @patch("nova.tasks.transcript_index_tasks.compute_transcript_chunk_embedding_task.delay")
    def test_index_transcript_append_updates_existing_chunk_when_hash_changes(self, mocked_delay):
        m1 = self.thread.add_message("Initial message", actor=Actor.USER)
        seg = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=m1.created_at.date(),
            starts_at_message=m1,
        )
        chunk = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=seg,
            start_message=m1,
            end_message=m1,
            content_text="stale",
            content_hash=TranscriptChunk.compute_hash("stale", m1.id, m1.id),
            token_estimate=1,
        )

        result = _index_transcript_append(day_segment_id=seg.id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["created"], 0)
        chunk.refresh_from_db()
        self.assertIn("User: Initial message", chunk.content_text)
        self.assertNotEqual(chunk.content_hash, TranscriptChunk.compute_hash("stale", m1.id, m1.id))

        emb = TranscriptChunkEmbedding.objects.get(transcript_chunk=chunk)
        self.assertEqual(emb.state, "pending")
        mocked_delay.assert_called_once_with(emb.id)

