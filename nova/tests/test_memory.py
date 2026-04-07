from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from nova.memory.service import (
    archive_memory_path,
    find_memory_paths,
    list_memory_dir_entries,
    memory_is_dir,
    memory_path_exists,
    mkdir_memory_dir,
    move_memory_path,
    parse_memory_virtual_path,
    read_memory_text,
    search_memory_items,
    write_memory_document,
)
from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryChunkEmbeddingState
from nova.models.Message import Actor
from nova.models.Thread import Thread


User = get_user_model()


class MemoryBuiltinCapabilityTests(TestCase):
    def test_memory_tool_registration(self):
        from nova.plugins.builtins import get_available_tool_types

        tool_types = get_available_tool_types()
        self.assertIn("memory", tool_types)
        self.assertEqual(tool_types["memory"]["name"], "Memory")


class MemoryDocumentServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-vfs-user",
            email="memory-vfs@example.com",
            password="testpass123",
        )
        self.thread = Thread.objects.create(user=self.user, subject="Memory source thread")
        self.source_message = self.thread.add_message("Remember this", actor=Actor.USER)

    def test_parse_memory_virtual_path_supports_free_form_markdown_and_directories(self):
        root = parse_memory_virtual_path("/memory")
        readme = parse_memory_virtual_path("/memory/README.md")
        directory = parse_memory_virtual_path("/memory/projects/client-a")
        item = parse_memory_virtual_path("/memory/projects/client-a/notes.md")

        self.assertEqual(root.kind, "root")
        self.assertEqual(readme.kind, "readme")
        self.assertEqual(directory.kind, "dir")
        self.assertEqual(item.kind, "item")

        with self.assertRaises(ValidationError):
            parse_memory_virtual_path("/memory/projects/client-a/notes.txt")

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_write_read_move_and_archive_memory_documents(self, mocked_provider):
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/projects")
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/projects/client-a")

        written = async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/projects/client-a/notes.md",
            text="# Client A\n\n## Constraints\nNeed weekly report\n\n## Contacts\nJane Doe",
            source_thread=self.thread,
            source_message=self.source_message,
        )
        content = async_to_sync(read_memory_text)(
            user=self.user,
            path="/memory/projects/client-a/notes.md",
        )
        moved = async_to_sync(move_memory_path)(
            user=self.user,
            source_path="/memory/projects/client-a/notes.md",
            destination_path="/memory/projects/client-a/brief.md",
        )
        document_before_archive = MemoryDocument.objects.get(
            user=self.user,
            virtual_path="/memory/projects/client-a/brief.md",
        )
        chunk_headings = list(
            document_before_archive.chunks.filter(status="active").order_by("position").values_list(
                "heading",
                flat=True,
            )
        )
        archived = async_to_sync(archive_memory_path)(user=self.user, path=moved)

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/projects/client-a/brief.md")

        self.assertEqual(written.path, "/memory/projects/client-a/notes.md")
        self.assertIn("Need weekly report", content)
        self.assertEqual(moved, "/memory/projects/client-a/brief.md")
        self.assertEqual(archived, "/memory/projects/client-a/brief.md")
        self.assertEqual(document.status, "archived")
        self.assertEqual(chunk_headings, ["Constraints", "Contacts"])
        mocked_provider.assert_awaited()

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_archive_directory_requires_it_to_be_empty(self, mocked_provider):
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/projects")
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/projects/notes.md",
            text="# Notes\n\nKeep this",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        with self.assertRaisesMessage(ValidationError, "Directory not empty"):
            async_to_sync(archive_memory_path)(user=self.user, path="/memory/projects")

        async_to_sync(archive_memory_path)(user=self.user, path="/memory/projects/notes.md")
        archived_dir = async_to_sync(archive_memory_path)(user=self.user, path="/memory/projects")

        self.assertEqual(archived_dir, "/memory/projects")
        mocked_provider.assert_awaited()

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_list_entries_find_paths_and_directory_state_reflect_free_form_layout(self, mocked_provider):
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/projects")
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/projects/client-a.md",
            text="# Client A\n\nImportant constraints",
            source_thread=self.thread,
            source_message=self.source_message,
        )
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/note.md",
            text="# Note\n\nFree-form memory",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        root_entries = async_to_sync(list_memory_dir_entries)(user=self.user, path="/memory")
        project_entries = async_to_sync(list_memory_dir_entries)(user=self.user, path="/memory/projects")
        all_paths = async_to_sync(find_memory_paths)(user=self.user, start_path="/memory", term="client")

        self.assertTrue(async_to_sync(memory_path_exists)(user=self.user, path="/memory/note.md"))
        self.assertTrue(async_to_sync(memory_is_dir)(user=self.user, path="/memory/projects"))
        self.assertEqual(
            [entry["name"] for entry in root_entries],
            ["README.md", "note.md", "projects"],
        )
        self.assertEqual([entry["name"] for entry in project_entries], ["client-a.md"])
        self.assertIn("/memory/projects/client-a.md", all_paths)
        mocked_provider.assert_awaited()

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_search_memory_items_supports_under_filter_and_returns_sections(self, mocked_provider):
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/projects")
        async_to_sync(mkdir_memory_dir)(user=self.user, path="/memory/personal")
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/projects/client-a.md",
            text="# Client A\n\n## Constraints\nDeadline is Friday\n\n## Notes\nBudget approved",
            source_thread=self.thread,
            source_message=self.source_message,
        )
        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/personal/shopping.md",
            text="# Shopping\n\n## Groceries\nBuy fruit on Friday",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        result = async_to_sync(search_memory_items)(
            query="deadline",
            user=self.user,
            under="/memory/projects",
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["path"], "/memory/projects/client-a.md")
        self.assertEqual(result["results"][0]["section_heading"], "Constraints")
        self.assertIn("Deadline is Friday", result["results"][0]["snippet"])
        mocked_provider.assert_awaited()

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_large_markdown_documents_are_split_into_multiple_chunks(self, mocked_provider):
        large_paragraph = " ".join(f"word-{index}" for index in range(700))

        async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/large.md",
            text=f"# Large\n\n## Oversized\n{large_paragraph}",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/large.md")
        chunks = list(document.chunks.filter(status="active").order_by("position"))

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(isinstance(chunk, MemoryChunk) for chunk in chunks))
        self.assertTrue(all(chunk.token_count > 0 for chunk in chunks))
        mocked_provider.assert_awaited()

    @patch("nova.tasks.memory_tasks.compute_memory_chunk_embedding_task.delay")
    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock)
    def test_write_memory_document_creates_embeddings_and_queues_immediately_when_provider_is_available(
        self,
        mocked_provider,
        mocked_delay,
    ):
        mocked_provider.return_value = object()

        written = async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/queued.md",
            text="# Queued\n\n## Constraints\nNeed follow-up",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/queued.md")
        chunks = list(document.chunks.filter(status="active").order_by("position"))
        embeddings = list(
            MemoryChunkEmbedding.objects.filter(chunk__document=document).order_by("chunk__position")
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0].state, MemoryChunkEmbeddingState.PENDING)
        self.assertEqual(mocked_delay.call_count, 1)
        self.assertEqual(mocked_delay.call_args.args[0], chunks[0].id)
        self.assertEqual(written.warnings, ())

    @patch("nova.tasks.memory_tasks.compute_memory_chunk_embedding_task.delay")
    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_write_memory_document_creates_pending_embeddings_without_immediate_queue_when_provider_is_unavailable(
        self,
        mocked_provider,
        mocked_delay,
    ):
        written = async_to_sync(write_memory_document)(
            user=self.user,
            path="/memory/pending.md",
            text="# Pending\n\n## Notes\nWait for embeddings provider",
            source_thread=self.thread,
            source_message=self.source_message,
        )

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/pending.md")
        chunks = list(document.chunks.filter(status="active"))
        embeddings = list(MemoryChunkEmbedding.objects.filter(chunk__document=document))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0].state, MemoryChunkEmbeddingState.PENDING)
        mocked_delay.assert_not_called()
        self.assertEqual(written.warnings, ())
        mocked_provider.assert_awaited()

    @patch(
        "nova.tasks.memory_tasks.compute_memory_chunk_embedding_task.delay",
        side_effect=RuntimeError("broker down"),
    )
    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock)
    def test_write_memory_document_logs_and_returns_warning_when_enqueue_fails(
        self,
        mocked_provider,
        mocked_delay,
    ):
        mocked_provider.return_value = object()

        with self.assertLogs("nova.memory.service", level="WARNING") as captured:
            written = async_to_sync(write_memory_document)(
                user=self.user,
                path="/memory/warn.md",
                text="# Warn\n\nQueue failure should not block writes",
                source_thread=self.thread,
                source_message=self.source_message,
            )

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/warn.md")
        embeddings = list(MemoryChunkEmbedding.objects.filter(chunk__document=document))

        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0].state, MemoryChunkEmbeddingState.PENDING)
        self.assertEqual(mocked_delay.call_count, 1)
        self.assertEqual(
            written.warnings,
            (
                "Warning: memory embeddings remain pending because background calculation could not be queued immediately.",
            ),
        )
        self.assertTrue(
            any("memory_embedding_enqueue_failed" in message for message in captured.output)
        )
