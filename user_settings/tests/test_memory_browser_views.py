from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryChunkEmbeddingState, MemoryRecordStatus


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

        self.active_document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path="/memory/work.md",
            title="Work",
            content_markdown="# Work\n\nParis office opens at 9am",
            status=MemoryRecordStatus.ACTIVE,
        )
        self.archived_document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path="/memory/personal.md",
            title="Personal",
            content_markdown="# Personal\n\nOld archived memory",
            status=MemoryRecordStatus.ARCHIVED,
        )
        self.other_user_document = MemoryDocument.objects.create(
            user=self.other_user,
            virtual_path="/memory/other.md",
            title="Other",
            content_markdown="# Other\n\nOther user memory",
            status=MemoryRecordStatus.ACTIVE,
        )

        active_chunk = MemoryChunk.objects.create(
            document=self.active_document,
            heading="Work",
            anchor="work",
            position=0,
            content_text="Paris office opens at 9am",
            token_count=5,
            status=MemoryRecordStatus.ACTIVE,
        )
        archived_chunk = MemoryChunk.objects.create(
            document=self.archived_document,
            heading="Personal",
            anchor="personal",
            position=0,
            content_text="Old archived memory",
            token_count=3,
            status=MemoryRecordStatus.ACTIVE,
        )
        other_chunk = MemoryChunk.objects.create(
            document=self.other_user_document,
            heading="Other",
            anchor="other",
            position=0,
            content_text="Other user memory",
            token_count=3,
            status=MemoryRecordStatus.ACTIVE,
        )

        MemoryChunkEmbedding.objects.create(
            chunk=active_chunk,
            state=MemoryChunkEmbeddingState.READY,
            vector=[0.1] * 1024,
        )
        MemoryChunkEmbedding.objects.create(
            chunk=archived_chunk,
            state=MemoryChunkEmbeddingState.ERROR,
            error="boom",
        )
        MemoryChunkEmbedding.objects.create(
            chunk=other_chunk,
            state=MemoryChunkEmbeddingState.PENDING,
        )

    def test_login_required(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_default_view_lists_only_active_documents_for_current_user(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/memory_items_table.html")
        documents = [row["document"] for row in response.context["documents"]]
        self.assertEqual(documents, [self.active_document])
        self.assertFalse(response.context["include_archived"])
        self.assertContains(response, "/memory/work.md")
        self.assertNotContains(response, "/memory/personal.md")
        self.assertNotContains(response, "/memory/other.md")

    def test_include_archived_accepts_truthy_flag_and_sets_context(self):
        response = self.client.get(self.url, {"include_archived": "yes"})

        self.assertEqual(response.status_code, 200)
        documents = [row["document"] for row in response.context["documents"]]
        self.assertEqual(documents, [self.archived_document, self.active_document])
        self.assertTrue(response.context["include_archived"])
        self.assertContains(response, "/memory/personal.md")
        self.assertContains(response, "archived")

    def test_query_filter_matches_path(self):
        response = self.client.get(
            self.url,
            {"include_archived": "1", "q": "work"},
        )

        self.assertEqual(response.status_code, 200)
        documents = [row["document"] for row in response.context["documents"]]
        self.assertEqual(documents, [self.active_document])
        self.assertContains(response, "/memory/work.md")
        self.assertNotContains(response, "/memory/personal.md")

    def test_query_filter_matches_content(self):
        response = self.client.get(
            self.url,
            {"include_archived": "true", "q": "archived"},
        )

        self.assertEqual(response.status_code, 200)
        documents = [row["document"] for row in response.context["documents"]]
        self.assertEqual(documents, [self.archived_document])
        self.assertContains(response, "/memory/personal.md")
        self.assertNotContains(response, "/memory/work.md")
