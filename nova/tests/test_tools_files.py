from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

class FilesToolsTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.files_tools = importlib.import_module("nova.tools.files")

    async def test_list_files_returns_no_files_message(self):
        fake_thread = SimpleNamespace(id=1)
        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_thread):
            with patch("nova.tools.files.async_filter_files", new_callable=AsyncMock, return_value=None):
                result = await self.files_tools.list_files(thread_id=1, user=SimpleNamespace(id=10))
        self.assertEqual(result, "No files in this thread.")

    async def test_create_file_success(self):
        fake_thread = SimpleNamespace(id=2)
        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_thread):
            with patch(
                "nova.tools.files.batch_upload_files",
                new_callable=AsyncMock,
                return_value=([{"id": 99}], []),
            ):
                result = await self.files_tools.create_file(2, SimpleNamespace(id=1), "test.txt", "hello")
        self.assertEqual(result, "File created: ID 99")

    async def test_create_file_returns_pipeline_errors(self):
        fake_thread = SimpleNamespace(id=2)
        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_thread):
            with patch(
                "nova.tools.files.batch_upload_files",
                new_callable=AsyncMock,
                return_value=([], ["invalid MIME"]),
            ):
                result = await self.files_tools.create_file(2, SimpleNamespace(id=1), "bad.bin", "x")
        self.assertIn("Error creating file", result)
        self.assertIn("invalid MIME", result)

    async def test_create_file_handles_pipeline_exception(self):
        fake_thread = SimpleNamespace(id=2)
        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_thread):
            with patch(
                "nova.tools.files.batch_upload_files",
                new_callable=AsyncMock,
                side_effect=RuntimeError("upload failed"),
            ):
                result = await self.files_tools.create_file(2, SimpleNamespace(id=1), "bad.bin", "x")
        self.assertIn("Error creating file: upload failed", result)

    async def test_read_file_chunk_denies_cross_thread_access(self):
        fake_user = SimpleNamespace(id=1)
        fake_agent = SimpleNamespace(
            thread=SimpleNamespace(id=100),
            user=fake_user,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=1000)),
        )
        fake_file = SimpleNamespace(thread=SimpleNamespace(id=200), user=fake_user)

        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_file):
            with patch(
                "nova.tools.files.async_get_threadid_and_user",
                new_callable=AsyncMock,
                side_effect=[(100, fake_user), (200, fake_user)],
            ):
                result = await self.files_tools.read_file_chunk(fake_agent, file_id=1)

        self.assertEqual(result, "Permission denied.")

    async def test_read_file_chunk_rejects_too_large_chunk(self):
        fake_user = SimpleNamespace(id=1)
        fake_agent = SimpleNamespace(
            thread=SimpleNamespace(id=100),
            user=fake_user,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=100)),
        )
        fake_file = SimpleNamespace(thread=SimpleNamespace(id=100), user=fake_user)

        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_file):
            with patch(
                "nova.tools.files.async_get_threadid_and_user",
                new_callable=AsyncMock,
                side_effect=[(100, fake_user), (100, fake_user)],
            ):
                result = await self.files_tools.read_file_chunk(fake_agent, file_id=1, chunk_size=1000)

        self.assertIn("Chunk too large", result)

    async def test_read_image_rejects_non_image_files(self):
        fake_user = SimpleNamespace(id=1)
        fake_agent = SimpleNamespace(thread=SimpleNamespace(id=100), user=fake_user)
        fake_file = SimpleNamespace(
            thread=SimpleNamespace(id=100),
            user=fake_user,
            mime_type="text/plain",
        )

        with patch("nova.tools.files.async_get_object_or_404", new_callable=AsyncMock, return_value=fake_file):
            with patch(
                "nova.tools.files.async_get_threadid_and_user",
                new_callable=AsyncMock,
                side_effect=[(100, fake_user), (100, fake_user)],
            ):
                message, artifact = await self.files_tools.read_image(fake_agent, file_id=1)

        self.assertIn("not an image", message.lower())
        self.assertIsNone(artifact)

    async def test_get_functions_returns_empty_when_no_thread_context(self):
        fake_agent = SimpleNamespace(thread=None, user=SimpleNamespace(id=1))
        with patch("nova.tools.files.async_get_threadid_and_user", new_callable=AsyncMock, return_value=(None, fake_agent.user)):
            tools = await self.files_tools.get_functions(fake_agent)
        self.assertEqual(tools, [])

    async def test_get_functions_exposes_expected_tool_names(self):
        fake_user = SimpleNamespace(id=1)
        fake_agent = SimpleNamespace(thread=SimpleNamespace(id=5), user=fake_user)
        with patch("nova.tools.files.async_get_threadid_and_user", new_callable=AsyncMock, return_value=(5, fake_user)):
            tools = await self.files_tools.get_functions(fake_agent)

        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {"file_ls", "file_get_url", "file_read_chunk", "file_create", "file_delete", "file_read_image"},
        )
