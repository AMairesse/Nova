from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Memory import (
    MemoryEmbeddingState,
    MemoryItem,
    MemoryItemEmbedding,
    MemoryItemStatus,
    MemoryTheme,
)


User = get_user_model()


class MemoryBrowserViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-browser-user",
            email="memory-browser@example.com",
            password="pass123",
        )
        self.other_user = User.objects.create_user(
            username="memory-browser-other",
            email="memory-browser-other@example.com",
            password="pass123",
        )
        self.client.login(username="memory-browser-user", password="pass123")
        self.url = reverse("user_settings:memory-items")

        self.work_theme = MemoryTheme.objects.create(
            user=self.user,
            slug="work",
            display_name="Work",
        )
        self.personal_theme = MemoryTheme.objects.create(
            user=self.user,
            slug="personal",
            display_name="Personal",
        )

        self.active_item = MemoryItem.objects.create(
            user=self.user,
            theme=self.work_theme,
            type="fact",
            content="Paris office opens at 9am",
            status=MemoryItemStatus.ACTIVE,
        )
        self.archived_item = MemoryItem.objects.create(
            user=self.user,
            theme=self.personal_theme,
            type="instruction",
            content="Old archived memory",
            status=MemoryItemStatus.ARCHIVED,
        )
        self.other_user_item = MemoryItem.objects.create(
            user=self.other_user,
            type="fact",
            content="Other user memory",
            status=MemoryItemStatus.ACTIVE,
        )

        MemoryItemEmbedding.objects.create(
            user=self.user,
            item=self.active_item,
            state=MemoryEmbeddingState.READY,
            vector=[0.1] * 1024,
        )
        MemoryItemEmbedding.objects.create(
            user=self.user,
            item=self.archived_item,
            state=MemoryEmbeddingState.ERROR,
            error="boom",
        )
        MemoryItemEmbedding.objects.create(
            user=self.other_user,
            item=self.other_user_item,
            state=MemoryEmbeddingState.PENDING,
        )

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_default_view_lists_only_active_items_for_current_user(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/memory_items_table.html")
        items = list(response.context["items"])
        self.assertEqual(items, [self.active_item])
        self.assertFalse(response.context["include_archived"])
        self.assertContains(response, "Paris office opens at 9am")
        self.assertNotContains(response, "Old archived memory")
        self.assertNotContains(response, "Other user memory")

    def test_include_archived_accepts_truthy_flag_and_sets_context(self):
        response = self.client.get(self.url, {"include_archived": "yes"})

        self.assertEqual(response.status_code, 200)
        items = list(response.context["items"])
        self.assertEqual(items, [self.archived_item, self.active_item])
        self.assertTrue(response.context["include_archived"])
        self.assertContains(response, "Old archived memory")
        self.assertContains(response, "archived")

    def test_theme_filter_is_case_insensitive(self):
        response = self.client.get(
            self.url,
            {"include_archived": "1", "theme": "WORK"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["items"]), [self.active_item])
        self.assertContains(response, "Paris office opens at 9am")
        self.assertNotContains(response, "Old archived memory")

    def test_query_filter_matches_content(self):
        response = self.client.get(
            self.url,
            {"include_archived": "true", "q": "archived"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["items"]), [self.archived_item])
        self.assertContains(response, "Old archived memory")
        self.assertNotContains(response, "Paris office opens at 9am")
