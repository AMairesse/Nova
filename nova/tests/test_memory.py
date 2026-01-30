"""Tests for the memory builtin tool.

NOTE: Memory tool was redesigned (v2) to use structured models + search.
The old Markdown theme slicing helpers are no longer present.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from asgiref.sync import async_to_sync
from types import SimpleNamespace

from nova.models.Memory import MemoryItem, MemoryItemEmbedding, MemoryTheme
from nova.tools.builtins.memory import add, get, list_themes, search


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
        emb = MemoryItemEmbedding.objects.get(item=item, user=self.user)
        self.assertEqual(emb.state, "pending")

    def test_get_returns_item(self):
        theme = MemoryTheme.objects.create(user=self.user, slug="work", display_name="Work")
        item = MemoryItem.objects.create(user=self.user, theme=theme, type="fact", content="Company is X")
        MemoryItemEmbedding.objects.create(user=self.user, item=item, state="pending", dimensions=1024)

        out = async_to_sync(get)(item.id, self.agent)
        self.assertEqual(out["id"], item.id)
        self.assertEqual(out["theme"], "work")

    def test_list_themes_returns_json(self):
        MemoryTheme.objects.create(user=self.user, slug="personal", display_name="Personal")
        out = async_to_sync(list_themes)(self.agent)
        self.assertIn("themes", out)
        self.assertTrue(any(t["slug"] == "personal" for t in out["themes"]))

    def test_search_finds_item(self):
        theme = MemoryTheme.objects.create(user=self.user, slug="personal", display_name="Personal")
        MemoryItem.objects.create(user=self.user, theme=theme, type="fact", content="Name is Alice")
        out = async_to_sync(search)(query="Alice", agent=self.agent)
        self.assertIn("results", out)
        self.assertGreaterEqual(len(out["results"]), 1)


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
