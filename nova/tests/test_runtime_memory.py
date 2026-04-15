from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryDirectory import MemoryDirectory
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryRecordStatus
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import TerminalCommandError

from .runtime_command_base import TerminalExecutorCommandTestCase


class MemoryCommandTests(TerminalExecutorCommandTestCase):
    def test_memory_mount_is_visible_only_when_memory_capability_is_enabled(self):
        plain_executor = self._build_executor()
        memory_executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        plain_listing = async_to_sync(plain_executor.execute)("ls /")
        memory_listing = async_to_sync(memory_executor.execute)("ls /")

        self.assertNotIn("memory/", plain_listing)
        self.assertIn("memory/", memory_listing)

    def test_memory_paths_are_reserved_without_memory_capability(self):
        executor = self._build_executor()

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("touch /memory/editor.md")
        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)('tee /memory/editor.md --text "Vim"')

    def test_memory_mount_supports_ls_cat_and_grep(self):
        MemoryDirectory.objects.create(
            user=self.user,
            virtual_path="/memory/projects",
            status=MemoryRecordStatus.ACTIVE,
        )
        document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path="/memory/projects/editor.md",
            title="Editor",
            content_markdown="# Editor\n\nPreferred editor is Vim",
            status=MemoryRecordStatus.ACTIVE,
        )
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        memory_root = async_to_sync(executor.execute)("ls /memory")
        memory_theme = async_to_sync(executor.execute)("ls /memory/projects")
        memory_doc = async_to_sync(executor.execute)("cat /memory/projects/editor.md")
        grep_result = async_to_sync(executor.execute)('grep -r -n "Vim" /memory')

        self.assertIn("README.md", memory_root)
        self.assertIn("projects/", memory_root)
        self.assertIn("editor.md", memory_theme)
        self.assertIn("Preferred editor is Vim", memory_doc)
        self.assertIn("/memory/projects/editor.md", grep_result)
        self.assertEqual(document.id, MemoryDocument.objects.get(id=document.id).id)

    def test_tee_and_rm_manage_memory_items(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        written = async_to_sync(executor.execute)('tee /memory/editor.md --text "# Editor\\n\\nVim"')
        content = async_to_sync(executor.execute)("cat /memory/editor.md")
        removed = async_to_sync(executor.execute)("rm /memory/editor.md")

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/editor.md")
        self.assertIn("/memory/editor.md", written)
        self.assertIn("Vim", content)
        self.assertEqual(removed, "Removed /memory/editor.md")
        self.assertEqual(document.status, MemoryRecordStatus.ARCHIVED)

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_memory_tee_decodes_escaped_newlines_and_builds_chunks(self, mocked_provider):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        result = async_to_sync(executor.execute)(
            'tee /memory/calendrier.md --text "# Calendrier\\n\\n## Preference\\nTexte"'
        )

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/calendrier.md")
        chunks = list(MemoryChunk.objects.filter(document=document).order_by("position"))

        self.assertIn("/memory/calendrier.md", result)
        self.assertIn("\n\n## Preference\nTexte", document.content_markdown)
        self.assertNotIn("\\n", document.content_markdown)
        self.assertTrue(any((chunk.heading or "") == "Preference" for chunk in chunks))
        self.assertGreater(len(chunks), 0)
        mocked_provider.assert_awaited()

    def test_memory_write_surfaces_embedding_queue_warning_in_terminal_output(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )
        executor.vfs.write_file = AsyncMock(
            return_value=SimpleNamespace(
                path="/memory/editor.md",
                warnings=(
                    "Warning: memory embeddings remain pending because background calculation could not be queued immediately.",
                ),
            )
        )

        result = async_to_sync(executor.execute)(
            'tee /memory/editor.md --text "# Editor\\n\\nVim"'
        )

        self.assertIn("Wrote", result)
        self.assertIn("/memory/editor.md", result)
        self.assertIn("memory embeddings remain pending", result)
        executor.vfs.write_file.assert_awaited_once()

    def test_touch_and_mv_manage_memory_items(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        async_to_sync(executor.execute)("mkdir /memory/tools")
        created = async_to_sync(executor.execute)("touch /memory/editor.md")
        moved = async_to_sync(executor.execute)("mv /memory/editor.md /memory/tools/editor.md")
        content = async_to_sync(executor.execute)("cat /memory/tools/editor.md")

        document = MemoryDocument.objects.get(user=self.user, virtual_path="/memory/tools/editor.md")
        self.assertEqual(created, "Created empty file /memory/editor.md")
        self.assertEqual(moved, "Moved to /memory/tools/editor.md")
        self.assertEqual(content, "")
        self.assertEqual(document.virtual_path, "/memory/tools/editor.md")

    def test_memory_supports_nested_directories_when_created(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        async_to_sync(executor.execute)("mkdir /memory/preferences")
        async_to_sync(executor.execute)("mkdir /memory/preferences/editors")
        written = async_to_sync(executor.execute)(
            'tee /memory/preferences/editors/vim.md --text "# Vim\\n\\nFast editor"'
        )

        self.assertIn("/memory/preferences/editors/vim.md", written)

    def test_memory_search_formats_results_with_paths(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        with patch(
            "nova.runtime.commands.memory.search_memory_items",
            new_callable=AsyncMock,
            return_value={
                "results": [
                    {
                        "path": "/memory/projects/editor.md",
                        "section_heading": "Editor",
                        "section_anchor": "editor",
                        "snippet": "Uses Vim",
                    }
                ],
                "notes": [],
            },
        ) as mocked_search:
            result = async_to_sync(executor.execute)(
                'memory search "editor preference" --limit 2 --under /memory/projects'
            )

        self.assertIn("/memory/projects/editor.md", result)
        self.assertIn("Uses Vim", result)
        self.assertEqual(mocked_search.await_args.kwargs["query"], "editor preference")
        self.assertEqual(mocked_search.await_args.kwargs["under"], "/memory/projects")
