"""Tests for the memory builtin tool.

NOTE: Memory tool was redesigned (v2) to use structured models + search.
The old Markdown theme slicing helpers are no longer present.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from asgiref.sync import async_to_sync
from django.utils import timezone

import nova.tools.builtins.memory as memory_mod
from nova.models.Memory import MemoryItem, MemoryItemEmbedding, MemoryItemStatus, MemoryTheme
from nova.tools.builtins.memory import (
    _get_default_theme_slug,
    _normalize_theme_slug,
    add,
    archive,
    get,
    get_functions,
    get_prompt_instructions,
    list_themes,
    search,
)


User = get_user_model()


class MemoryToolV2Tests(TestCase):
    """Test the v2 memory tool surface (structured store)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.agent = SimpleNamespace(user=self.user)

    def test_theme_slug_helpers_normalize_and_validate(self):
        self.assertEqual(_normalize_theme_slug(" Work Notes "), "work-notes")
        self.assertEqual(_get_default_theme_slug(), "general")

        with self.assertRaisesMessage(ValidationError, "theme must be a non-empty string"):
            _normalize_theme_slug("   ")

    def test_add_creates_item_and_pending_embedding(self):
        out = async_to_sync(add)(
            type="fact",
            content="User's favorite IDE is VSCode",
            theme="preferences",
            tags=["dev"],
            agent=self.agent,
        )
        self.assertIn("id", out)
        item = MemoryItem.objects.get(id=out["id"], user=self.user)
        self.assertEqual(item.type, "fact")
        self.assertEqual(item.theme.slug, "preferences")
        # Embeddings are optional: if no provider configured, tool should still work.
        emb = MemoryItemEmbedding.objects.filter(item=item, user=self.user).first()
        if emb:
            self.assertEqual(emb.state, "pending")

    @patch("nova.tools.builtins.memory.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_add_validates_inputs_and_uses_default_theme(self, mocked_provider):
        with self.assertRaisesMessage(ValidationError, "Invalid memory item type"):
            async_to_sync(add)(type="invalid", content="Hello", agent=self.agent)

        with self.assertRaisesMessage(ValidationError, "content must be a non-empty string"):
            async_to_sync(add)(type="fact", content="   ", agent=self.agent)

        out = async_to_sync(add)(
            type="fact",
            content="Store this note",
            tags=["dev", "   ", 3, None],
            agent=self.agent,
        )

        item = MemoryItem.objects.get(id=out["id"], user=self.user)
        self.assertEqual(item.theme.slug, "general")
        self.assertEqual(item.tags, ["dev"])
        self.assertIsNone(out["embedding_state"])
        mocked_provider.assert_awaited()

    @patch("nova.tools.builtins.memory.aget_embeddings_provider", new_callable=AsyncMock)
    def test_add_with_embeddings_enabled_tolerates_enqueue_failures(self, mocked_provider):
        mocked_provider.return_value = object()

        with patch("nova.tasks.memory_tasks.compute_memory_item_embedding_task") as mocked_task:
            mocked_task.delay.side_effect = RuntimeError("celery unavailable")
            out = async_to_sync(add)(
                type="fact",
                content="Remember this",
                theme="profile",
                agent=self.agent,
            )

        item = MemoryItem.objects.get(id=out["id"], user=self.user)
        embedding = MemoryItemEmbedding.objects.get(item=item, user=self.user)
        self.assertEqual(item.theme.slug, "profile")
        self.assertEqual(out["embedding_state"], "pending")
        self.assertEqual(embedding.state, "pending")

    def test_get_returns_item(self):
        theme = MemoryTheme.objects.create(user=self.user, slug="work", display_name="Work")
        item = MemoryItem.objects.create(user=self.user, theme=theme, type="fact", content="Company is X")
        MemoryItemEmbedding.objects.create(user=self.user, item=item, state="pending", dimensions=1024)

        out = async_to_sync(get)(item.id, self.agent)
        self.assertEqual(out["id"], item.id)
        self.assertEqual(out["theme"], "work")

    def test_get_returns_not_found_for_unknown_item(self):
        self.assertEqual(async_to_sync(get)(999999, self.agent), {"error": "not_found"})

    def test_list_themes_returns_json(self):
        MemoryTheme.objects.create(user=self.user, slug="personal", display_name="Personal")
        out = async_to_sync(list_themes)(self.agent)
        self.assertIn("themes", out)
        self.assertFalse(any(t["slug"] == "personal" for t in out["themes"]))

    def test_list_themes_defaults_to_active_items_only(self):
        active_theme = MemoryTheme.objects.create(user=self.user, slug="active-theme", display_name="Active")
        archived_theme = MemoryTheme.objects.create(user=self.user, slug="archived-theme", display_name="Archived")
        MemoryItem.objects.create(user=self.user, theme=active_theme, type="fact", content="still relevant")
        MemoryItem.objects.create(
            user=self.user,
            theme=archived_theme,
            type="fact",
            content="deprecated",
            status="archived",
        )

        out = async_to_sync(list_themes)(self.agent)
        slugs = {t["slug"] for t in out["themes"]}
        self.assertIn("active-theme", slugs)
        self.assertNotIn("archived-theme", slugs)

    def test_list_themes_can_include_archived_items(self):
        archived_theme = MemoryTheme.objects.create(user=self.user, slug="archived-theme", display_name="Archived")
        MemoryItem.objects.create(
            user=self.user,
            theme=archived_theme,
            type="fact",
            content="deprecated",
            status="archived",
        )

        out = async_to_sync(list_themes)(self.agent, status="any")
        self.assertTrue(any(t["slug"] == "archived-theme" for t in out["themes"]))

    def test_list_themes_invalid_status_falls_back_to_active(self):
        active_theme = MemoryTheme.objects.create(user=self.user, slug="active", display_name="Active")
        archived_theme = MemoryTheme.objects.create(user=self.user, slug="archived", display_name="Archived")
        MemoryItem.objects.create(user=self.user, theme=active_theme, type="fact", content="kept")
        MemoryItem.objects.create(
            user=self.user,
            theme=archived_theme,
            type="fact",
            content="old",
            status=MemoryItemStatus.ARCHIVED,
        )

        out = async_to_sync(list_themes)(self.agent, status="unexpected")

        self.assertEqual([theme["slug"] for theme in out["themes"]], ["active"])

    def test_search_finds_item(self):
        theme = MemoryTheme.objects.create(user=self.user, slug="personal", display_name="Personal")
        MemoryItem.objects.create(user=self.user, theme=theme, type="fact", content="Name is Alice")
        out = async_to_sync(search)(query="Alice", agent=self.agent)
        self.assertIn("results", out)
        self.assertGreaterEqual(len(out["results"]), 1)

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock, return_value=None)
    def test_search_rejects_non_integer_limit(self, mocked_vector):
        with self.assertRaisesMessage(ValidationError, "limit must be an integer"):
            async_to_sync(search)(query="Alice", agent=self.agent, limit="bad")

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock, return_value=None)
    def test_search_match_all_applies_filters_and_clamps_limit(self, mocked_vector):
        work_theme = MemoryTheme.objects.create(user=self.user, slug="work-theme", display_name="Work Theme")
        other_theme = MemoryTheme.objects.create(user=self.user, slug="other-theme", display_name="Other Theme")
        recent_item = MemoryItem.objects.create(
            user=self.user,
            theme=work_theme,
            type="fact",
            content="Most recent work fact",
        )
        old_item = MemoryItem.objects.create(
            user=self.user,
            theme=work_theme,
            type="fact",
            content="Old work fact",
        )
        MemoryItem.objects.filter(id=old_item.id).update(created_at=timezone.now() - timedelta(days=9))
        MemoryItem.objects.create(
            user=self.user,
            theme=other_theme,
            type="fact",
            content="Other theme fact",
        )
        MemoryItem.objects.create(
            user=self.user,
            theme=work_theme,
            type="summary",
            content="Wrong type",
        )
        MemoryItem.objects.create(
            user=self.user,
            theme=work_theme,
            type="fact",
            content="Archived fact",
            status=MemoryItemStatus.ARCHIVED,
        )

        out = async_to_sync(search)(
            query="*",
            agent=self.agent,
            limit=0,
            theme="Work Theme",
            types=["fact", "invalid"],
            recency_days=3,
            status="unexpected",
        )

        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["id"], recent_item.id)
        self.assertEqual(
            out["notes"],
            ["match-all mode: empty query or '*' returns most recent items"],
        )
        self.assertEqual(out["results"][0]["signals"], {"fts": False, "semantic": False})

    def test_archive_returns_not_found_and_soft_deletes_items(self):
        self.assertEqual(async_to_sync(archive)(999999, self.agent), {"error": "not_found"})

        theme = MemoryTheme.objects.create(user=self.user, slug="work", display_name="Work")
        item = MemoryItem.objects.create(user=self.user, theme=theme, type="fact", content="Archive me")

        out = async_to_sync(archive)(item.id, self.agent)

        item.refresh_from_db()
        self.assertEqual(out, {"id": item.id, "status": MemoryItemStatus.ARCHIVED})
        self.assertEqual(item.status, MemoryItemStatus.ARCHIVED)

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock)
    def test_search_postgresql_branch_returns_no_matches_without_candidates(self, mocked_vector):
        mocked_vector.return_value = None

        with patch.object(memory_mod.connection, "vendor", "postgresql"), patch.object(
            memory_mod.MemoryItem.objects,
            "select_related",
            return_value=FakeMemoryPostgresQuerySet([]),
        ):
            out = async_to_sync(search)(query="deploy", agent=self.agent)

        self.assertEqual(out, {"results": [], "notes": ["no matches"]})

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock)
    def test_search_postgresql_branch_scores_fts_only_when_query_vector_is_missing(self, mocked_vector):
        mocked_vector.return_value = None
        item = SimpleNamespace(
            id=10,
            user=self.user,
            theme=SimpleNamespace(slug="ops"),
            type="fact",
            content="Deploy runbook",
            created_at=timezone.now(),
            fts_rank=0.7,
            distance=None,
            embedding=SimpleNamespace(state="ready"),
        )

        with patch.object(memory_mod.connection, "vendor", "postgresql"), patch.object(
            memory_mod.MemoryItem.objects,
            "select_related",
            return_value=FakeMemoryPostgresQuerySet([item]),
        ):
            out = async_to_sync(search)(query="deploy", agent=self.agent, limit=5)

        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["id"], 10)
        self.assertEqual(out["results"][0]["signals"], {"fts": True, "semantic": False})
        self.assertIsNone(out["results"][0]["score"]["cosine_distance"])

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock)
    def test_search_postgresql_branch_blends_semantic_and_fts_scores(self, mocked_vector):
        mocked_vector.return_value = [0.1, 0.2]
        newer = timezone.now()
        older = timezone.now() - timedelta(hours=1)
        items = [
            SimpleNamespace(
                id=21,
                user=self.user,
                theme=SimpleNamespace(slug="ops"),
                type="fact",
                content="Deploy checklist",
                created_at=newer,
                fts_rank=0.6,
                distance=0.15,
                embedding=SimpleNamespace(state="ready"),
            ),
            SimpleNamespace(
                id=22,
                user=self.user,
                theme=SimpleNamespace(slug="ops"),
                type="summary",
                content="Incident summary",
                created_at=older,
                fts_rank=0.3,
                distance=0.45,
                embedding=SimpleNamespace(state="ready"),
            ),
        ]

        with patch.object(memory_mod.connection, "vendor", "postgresql"), patch.object(
            memory_mod.MemoryItem.objects,
            "select_related",
            return_value=FakeMemoryPostgresQuerySet(items),
        ):
            out = async_to_sync(search)(query="deploy", agent=self.agent, limit=5)

        self.assertEqual({result["id"] for result in out["results"]}, {21, 22})
        self.assertTrue(all(result["signals"]["semantic"] for result in out["results"]))
        self.assertTrue(all(result["score"]["final"] >= 0.0 for result in out["results"]))
        self.assertTrue(all(result["score"]["cosine_distance"] is not None for result in out["results"]))


class MemoryIntegrationTest(TestCase):
    """Integration-level checks for signals and tool registration."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_memory_tool_registration(self):
        """
        Confirm that the memory tool is registered in the global tool registry
        with the expected key and display name.
        """
        from nova.tools import get_available_tool_types
        tool_types = get_available_tool_types()
        self.assertIn('memory', tool_types)
        self.assertEqual(tool_types['memory']['name'], 'Memory')

    def test_memory_prompt_instructions_do_not_reference_conversation_tools(self):
        hints = get_prompt_instructions()
        joined = " ".join(hints).lower()
        self.assertNotIn("conversation_search", joined)
        self.assertNotIn("conversation_get", joined)

    @patch("nova.tools.builtins.memory.resolve_query_vector", new_callable=AsyncMock, return_value=None)
    def test_get_functions_returns_wrapped_tool_surface(self, mocked_vector):
        agent = SimpleNamespace(user=self.user)
        tools = async_to_sync(get_functions)(tool=None, agent=agent)
        names = [tool.name for tool in tools]

        self.assertEqual(
            names,
            [
                "memory_search",
                "memory_add",
                "memory_get",
                "memory_list_themes",
                "memory_archive",
            ],
        )

        add_result = async_to_sync(tools[1].ainvoke)({"type": "fact", "content": "Remember this"})
        get_result = async_to_sync(tools[2].ainvoke)({"item_id": add_result["id"]})
        archive_result = async_to_sync(tools[4].ainvoke)({"item_id": add_result["id"]})

        self.assertEqual(get_result["id"], add_result["id"])
        self.assertEqual(archive_result["status"], MemoryItemStatus.ARCHIVED)


class FakeMemoryPostgresQuerySet:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, *args, **kwargs):
        items = list(self.items)
        user = kwargs.get("user")
        if user is not None:
            items = [item for item in items if getattr(item, "user", None) == user]
        if "status" in kwargs:
            items = [item for item in items if getattr(item, "status", MemoryItemStatus.ACTIVE) == kwargs["status"]]
        if "theme__slug" in kwargs:
            slug = kwargs["theme__slug"]
            items = [item for item in items if getattr(getattr(item, "theme", None), "slug", None) == slug]
        if "type__in" in kwargs:
            requested = set(kwargs["type__in"])
            items = [item for item in items if getattr(item, "type", None) in requested]
        if "created_at__gte" in kwargs:
            cutoff = kwargs["created_at__gte"]
            items = [item for item in items if getattr(item, "created_at", None) >= cutoff]
        if "id__in" in kwargs:
            ids = set(kwargs["id__in"])
            items = [item for item in items if getattr(item, "id", None) in ids]
        if "embedding__state" in kwargs:
            state = kwargs["embedding__state"]
            items = [
                item
                for item in items
                if getattr(getattr(item, "embedding", None), "state", None) == state
            ]
        if "fts_rank__gt" in kwargs:
            minimum = kwargs["fts_rank__gt"]
            items = [item for item in items if float(getattr(item, "fts_rank", 0.0) or 0.0) > minimum]
        return FakeMemoryPostgresQuerySet(items)

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

    def __getitem__(self, item):
        return self.items[item]
