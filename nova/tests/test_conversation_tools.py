from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from nova.continuous.tools import conversation_tools as conversation_tools_mod
from nova.continuous.tools.conversation_tools import (
    _focused_snippet,
    _local_lexical_anchor_window,
    _recency_multiplier,
    _sentence_spans,
    _tokenize_for_local_match,
    _trim_with_ellipses,
    _validate_limit_offset,
    conversation_get,
    conversation_search,
)
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.models.TranscriptChunk import TranscriptChunk


User = get_user_model()


class ConversationToolHelpersTests(TestCase):
    def test_validate_limit_offset_clamps_bounds(self):
        self.assertEqual(_validate_limit_offset(99, 999), (50, 500))
        self.assertEqual(_validate_limit_offset(-1, -5), (1, 0))

    def test_validate_limit_offset_rejects_non_integers(self):
        with self.assertRaisesMessage(ValidationError, "limit must be an integer"):
            _validate_limit_offset("bad", 0)

        with self.assertRaisesMessage(ValidationError, "offset must be an integer"):
            _validate_limit_offset(1, "bad")

    def test_recency_multiplier_buckets(self):
        now = timezone.now()

        self.assertEqual(_recency_multiplier(None), 0.8)
        self.assertEqual(_recency_multiplier(now - timedelta(hours=2)), 1.0)
        self.assertEqual(_recency_multiplier(now - timedelta(days=2)), 0.9)
        self.assertEqual(_recency_multiplier(now - timedelta(days=10)), 0.8)

    def test_tokenize_for_local_match_removes_stopwords(self):
        tokens = _tokenize_for_local_match("The deploy plan and the rollback notes")

        self.assertEqual(tokens, ["deploy", "plan", "rollback", "notes"])

    def test_trim_with_ellipses_adds_requested_markers(self):
        snippet = _trim_with_ellipses("  useful context  ", start_cut=True, end_cut=True)

        self.assertEqual(snippet, "… useful context …")

    def test_sentence_spans_falls_back_when_text_is_only_punctuation(self):
        spans = _sentence_spans("...")

        self.assertEqual(spans, [(0, 3, "...")])

    def test_local_lexical_anchor_window_handles_empty_and_short_inputs(self):
        self.assertEqual(_local_lexical_anchor_window("", "deploy"), "")
        self.assertEqual(
            _local_lexical_anchor_window("Short deploy note", "deploy", max_len=240),
            "Short deploy note",
        )

    def test_local_lexical_anchor_window_trims_when_query_has_no_usable_tokens(self):
        text = "alpha beta gamma delta " * 20

        snippet = _local_lexical_anchor_window(text, "the and", max_len=40)

        self.assertTrue(snippet.endswith(" …"))

    def test_local_lexical_anchor_window_falls_back_when_no_sentence_matches(self):
        text = ("the and or. " * 30).strip()

        snippet = _local_lexical_anchor_window(text, "deploy", max_len=40)

        self.assertTrue(snippet.endswith(" …"))

    def test_local_lexical_anchor_window_recenters_when_match_is_near_end(self):
        text = "x" * 100

        with patch(
            "nova.continuous.tools.conversation_tools._tokenize_for_local_match",
            side_effect=[["deploy"], ["deploy"]],
        ), patch(
            "nova.continuous.tools.conversation_tools._sentence_spans",
            return_value=[(95, 100, "deploy")],
        ):
            snippet = _local_lexical_anchor_window(text, "deploy checklist", max_len=20)

        self.assertEqual(snippet, "… " + ("x" * 20))
        self.assertTrue(snippet.startswith("… "))

    def test_focused_snippet_truncates_long_headline_with_marks(self):
        headline = "prefix " + ("x" * 60) + "<mark>deploy</mark>" + ("y" * 60)

        snippet = _focused_snippet(
            text="unused",
            query="deploy",
            headline=headline,
            max_len=50,
        )

        self.assertLessEqual(len(snippet), 54)
        self.assertTrue(snippet.endswith(" …"))


class ConversationToolsBaseTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="conversation-user",
            email="conversation@example.com",
            password="pass123",
        )
        self.other_user = User.objects.create_user(
            username="conversation-other",
            email="conversation-other@example.com",
            password="pass123",
        )
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        self.other_thread = Thread.objects.create(
            user=self.user,
            subject="Other continuous",
            mode=Thread.Mode.CONTINUOUS,
        )
        self.agent = SimpleNamespace(user=self.user, thread=self.thread)

        self.msg1 = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.USER,
            text="How should we deploy the API?",
        )
        self.msg2 = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.AGENT,
            text="Use a blue green deployment.",
        )
        self.msg3 = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.USER,
            text="What is the rollback plan?",
        )
        self.msg4 = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.AGENT,
            text="Rollback is automatic after failed health checks.",
        )
        self.msg5 = Message.objects.create(
            user=self.user,
            thread=self.thread,
            actor=Actor.USER,
            text="Archive this older note.",
        )
        self.other_thread_msg = Message.objects.create(
            user=self.user,
            thread=self.other_thread,
            actor=Actor.USER,
            text="Deploy secret for another thread",
        )
        self.other_user_msg = Message.objects.create(
            user=self.other_user,
            thread=Thread.objects.create(
                user=self.other_user,
                subject="Foreign",
                mode=Thread.Mode.CONTINUOUS,
            ),
            actor=Actor.USER,
            text="Foreign deploy note",
        )

        self.day_one = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=date.today(),
            starts_at_message=self.msg1,
            summary_markdown="Deployment summary for today's rollout.",
        )
        self.day_two = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=date.today() - timedelta(days=2),
            starts_at_message=self.msg3,
            summary_markdown="Rollback summary for the previous incident.",
        )
        self.old_day = DaySegment.objects.create(
            user=self.user,
            thread=self.thread,
            day_label=date.today() - timedelta(days=30),
            starts_at_message=self.msg5,
            summary_markdown="Very old archived deploy summary.",
        )

        self.chunk_one = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=self.day_one,
            start_message=self.msg1,
            end_message=self.msg2,
            content_text="User: deploy the API\nAgent: use blue green deployment",
            content_hash=TranscriptChunk.compute_hash(
                "User: deploy the API\nAgent: use blue green deployment",
                self.msg1.id,
                self.msg2.id,
            ),
            token_estimate=12,
        )
        self.chunk_two = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=self.day_two,
            start_message=self.msg3,
            end_message=self.msg4,
            content_text="User: rollback plan\nAgent: automatic rollback after failed checks",
            content_hash=TranscriptChunk.compute_hash(
                "User: rollback plan\nAgent: automatic rollback after failed checks",
                self.msg3.id,
                self.msg4.id,
            ),
            token_estimate=12,
        )
        self.old_chunk = TranscriptChunk.objects.create(
            user=self.user,
            thread=self.thread,
            day_segment=self.old_day,
            start_message=self.msg5,
            end_message=self.msg5,
            content_text="Old deploy note from last month",
            content_hash=TranscriptChunk.compute_hash(
                "Old deploy note from last month",
                self.msg5.id,
                self.msg5.id,
            ),
            token_estimate=8,
        )
        TranscriptChunk.objects.create(
            user=self.user,
            thread=self.other_thread,
            day_segment=None,
            start_message=self.other_thread_msg,
            end_message=self.other_thread_msg,
            content_text="Deploy secret for another thread",
            content_hash=TranscriptChunk.compute_hash(
                "Deploy secret for another thread",
                self.other_thread_msg.id,
                self.other_thread_msg.id,
            ),
            token_estimate=6,
        )


class ConversationGetTests(ConversationToolsBaseTest):
    def test_conversation_get_returns_summary_for_day_segment(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            day_segment_id=self.day_one.id,
        )

        self.assertEqual(out["day_segment_id"], self.day_one.id)
        self.assertEqual(out["summary_markdown"], self.day_one.summary_markdown)

    def test_conversation_get_returns_not_found_for_missing_summary_day(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            day_segment_id=999999,
        )

        self.assertEqual(out, {"error": "not_found"})

    def test_conversation_get_requires_a_target(self):
        out = async_to_sync(conversation_get)(agent=self.agent)

        self.assertEqual(out, {"error": "invalid_request"})

    def test_conversation_get_rejects_invalid_limit(self):
        with self.assertRaisesMessage(ValidationError, "limit must be an integer"):
            async_to_sync(conversation_get)(
                agent=self.agent,
                message_id=self.msg3.id,
                limit="bad",
            )

    def test_conversation_get_returns_not_found_for_unknown_day_filter(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=self.msg3.id,
            day_segment_id=999999,
        )

        self.assertEqual(out, {"error": "not_found"})

    def test_conversation_get_scopes_anchor_lookup_to_day_segment_window(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=self.msg4.id,
            day_segment_id=self.day_two.id,
            limit=2,
        )

        self.assertEqual(
            [message["message_id"] for message in out["messages"]],
            [self.msg3.id, self.msg4.id],
        )

    def test_conversation_get_returns_not_found_for_unknown_anchor(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=999999,
        )

        self.assertEqual(out, {"error": "not_found"})

    def test_conversation_get_returns_before_window(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=self.msg3.id,
            before_message_id=self.msg3.id,
            limit=2,
        )

        self.assertEqual(
            [message["message_id"] for message in out["messages"]],
            [self.msg1.id, self.msg2.id],
        )
        self.assertTrue(out["truncated"])

    def test_conversation_get_returns_after_window(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=self.msg2.id,
            after_message_id=self.msg2.id,
            limit=2,
        )

        self.assertEqual(
            [message["message_id"] for message in out["messages"]],
            [self.msg3.id, self.msg4.id],
        )
        self.assertTrue(out["truncated"])

    def test_conversation_get_returns_centered_window_scoped_to_thread(self):
        out = async_to_sync(conversation_get)(
            agent=self.agent,
            message_id=self.msg3.id,
            limit=3,
        )

        self.assertEqual(
            [message["message_id"] for message in out["messages"]],
            [self.msg2.id, self.msg3.id, self.msg4.id],
        )
        self.assertNotIn(self.other_thread_msg.id, [m["message_id"] for m in out["messages"]])


class ConversationSearchTests(ConversationToolsBaseTest):
    def test_conversation_search_rejects_empty_query(self):
        with self.assertRaisesMessage(ValidationError, "query must be a non-empty string"):
            async_to_sync(conversation_search)(query="   ", agent=self.agent)

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_rejects_invalid_day_format(self, mocked_vector):
        mocked_vector.return_value = None

        with self.assertRaisesMessage(ValidationError, "day must be YYYY-MM-DD"):
            async_to_sync(conversation_search)(
                query="deploy",
                day="2026/03/14",
                agent=self.agent,
            )

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_returns_no_matches_for_missing_day(self, mocked_vector):
        mocked_vector.return_value = None

        out = async_to_sync(conversation_search)(
            query="deploy",
            day="1999-01-01",
            agent=self.agent,
        )

        self.assertEqual(out, {"results": [], "notes": ["no matches"]})

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_filters_to_requested_day(self, mocked_vector):
        mocked_vector.return_value = None

        out = async_to_sync(conversation_search)(
            query="rollback",
            day=self.day_two.day_label.isoformat(),
            agent=self.agent,
            limit=10,
        )

        self.assertTrue(out["results"])
        self.assertTrue(all(result["day_segment_id"] == self.day_two.id for result in out["results"]))

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_applies_recency_filter_and_offset(self, mocked_vector):
        mocked_vector.return_value = None

        out = async_to_sync(conversation_search)(
            query="deploy",
            agent=self.agent,
            recency_days=7,
            limit=1,
            offset=1,
        )

        self.assertEqual(len(out["results"]), 1)
        joined = " ".join(str(value) for value in out["results"][0].values())
        self.assertNotIn("last month", joined.lower())
        self.assertNotIn("another thread", joined.lower())

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_postgresql_branch_with_semantic_candidates(self, mocked_vector):
        mocked_vector.return_value = [0.1, 0.2, 0.3]
        seg_qs = FakePostgresQuerySet(
            [
                SimpleNamespace(
                    id=101,
                    day_label=date.today(),
                    summary_markdown="Deploy summary",
                    updated_at=timezone.now(),
                    headline="<mark>deploy</mark> summary",
                    fts_rank=0.7,
                    distance=0.2,
                ),
            ]
        )
        chunk_qs = FakePostgresQuerySet(
            [
                SimpleNamespace(
                    id=202,
                    day_segment=SimpleNamespace(day_label=date.today()),
                    day_segment_id=55,
                    start_message_id=999,
                    content_text="Deploy details for the transcript chunk",
                    created_at=timezone.now(),
                    headline="<mark>deploy</mark> details",
                    fts_rank=0.5,
                    distance=0.4,
                ),
            ]
        )

        with patch.object(conversation_tools_mod.connection, "vendor", "postgresql"), patch(
            "nova.continuous.tools.conversation_tools.DaySegment.objects.filter",
            return_value=seg_qs,
        ), patch(
            "nova.continuous.tools.conversation_tools.TranscriptChunk.objects.filter",
            return_value=chunk_qs,
        ):
            out = async_to_sync(conversation_search)(
                query="deploy",
                agent=self.agent,
                limit=10,
            )

        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(out["results"][0]["kind"], "summary")
        self.assertEqual(out["results"][1]["kind"], "message")
        self.assertIn("semantic ranking enabled", out["notes"][0])

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_postgresql_branch_without_query_vector_uses_fts_only(
        self,
        mocked_vector,
    ):
        mocked_vector.return_value = None
        seg_qs = FakePostgresQuerySet(
            [
                SimpleNamespace(
                    id=101,
                    day_label=date.today(),
                    summary_markdown="Deploy summary",
                    updated_at=timezone.now(),
                    headline="<mark>deploy</mark> summary",
                    fts_rank=0.7,
                ),
            ]
        )
        chunk_qs = FakePostgresQuerySet(
            [
                SimpleNamespace(
                    id=202,
                    day_segment=SimpleNamespace(day_label=date.today()),
                    day_segment_id=55,
                    start_message_id=999,
                    content_text="Deploy details for the transcript chunk",
                    created_at=timezone.now(),
                    headline="<mark>deploy</mark> details",
                    fts_rank=0.5,
                ),
            ]
        )

        with patch.object(conversation_tools_mod.connection, "vendor", "postgresql"), patch(
            "nova.continuous.tools.conversation_tools.DaySegment.objects.filter",
            return_value=seg_qs,
        ), patch(
            "nova.continuous.tools.conversation_tools.TranscriptChunk.objects.filter",
            return_value=chunk_qs,
        ):
            out = async_to_sync(conversation_search)(
                query="deploy",
                agent=self.agent,
                limit=10,
            )

        self.assertEqual(len(out["results"]), 2)
        self.assertTrue(all(result["score"] is not None for result in out["results"]))

    @patch("nova.continuous.tools.conversation_tools.resolve_query_vector", new_callable=AsyncMock)
    def test_conversation_search_postgresql_branch_returns_no_matches_without_candidates(
        self,
        mocked_vector,
    ):
        mocked_vector.return_value = None
        seg_qs = FakePostgresQuerySet([])
        chunk_qs = FakePostgresQuerySet([])

        with patch.object(conversation_tools_mod.connection, "vendor", "postgresql"), patch(
            "nova.continuous.tools.conversation_tools.DaySegment.objects.filter",
            return_value=seg_qs,
        ), patch(
            "nova.continuous.tools.conversation_tools.TranscriptChunk.objects.filter",
            return_value=chunk_qs,
        ):
            out = async_to_sync(conversation_search)(
                query="deploy",
                agent=self.agent,
                limit=10,
            )

        self.assertEqual(out, {"results": [], "notes": ["no matches"]})

class FakePostgresQuerySet:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, **kwargs):
        items = self.items
        if "id__in" in kwargs:
            ids = set(kwargs["id__in"])
            items = [item for item in items if getattr(item, "id", None) in ids]
        if "day_label" in kwargs:
            day_label = kwargs["day_label"]
            items = [item for item in items if getattr(item, "day_label", None) == day_label]
        if "day_segment" in kwargs:
            day_segment = kwargs["day_segment"]
            items = [item for item in items if getattr(item, "day_segment", None) == day_segment]
        return FakePostgresQuerySet(items)

    def annotate(self, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def values_list(self, field, flat=False):
        return [getattr(item, field) for item in self.items]

    def select_related(self, *args):
        return self

    def first(self):
        return self.items[0] if self.items else None

    def __iter__(self):
        return iter(self.items)
