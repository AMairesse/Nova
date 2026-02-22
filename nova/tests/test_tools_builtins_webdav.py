from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.tests.factories import create_tool, create_tool_credential, create_user
from nova.tools.builtins import webdav


class WebDAVBuiltinsTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="nc-user", email="nc@example.com")
        self.tool = create_tool(
            self.user,
            name="WebDAV",
            tool_subtype="webdav",
            python_path="nova.tools.builtins.webdav",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "server_url": "https://cloud.example.com",
                "username": "alice",
                "app_password": "secret",
                "root_path": "/Documents",
            },
        )

    def test_build_webdav_url_encodes_segments(self):
        url = webdav._build_webdav_url("https://cloud.example.com/webdav/", "/A folder/report 1.txt")
        self.assertEqual(
            url,
            "https://cloud.example.com/webdav/A%20folder/report%201.txt",
        )

    def test_coerce_bool_supports_string_values(self):
        self.assertTrue(webdav._coerce_bool("true"))
        self.assertTrue(webdav._coerce_bool("1"))
        self.assertFalse(webdav._coerce_bool("false"))
        self.assertFalse(webdav._coerce_bool("0"))

    @patch("nova.tools.builtins.webdav._webdav_request", new_callable=AsyncMock)
    def test_list_files_parses_propfind_xml(self, mocked_request):
        mocked_request.return_value = (
            207,
            """
            <d:multistatus xmlns:d=\"DAV:\">
              <d:response>
                <d:href>/remote.php/dav/files/alice/Documents/</d:href>
                <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
              </d:response>
              <d:response>
                <d:href>/remote.php/dav/files/alice/Documents/notes.txt</d:href>
                <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>42</d:getcontentlength></d:prop></d:propstat>
              </d:response>
            </d:multistatus>
            """,
        )

        result = asyncio.run(webdav.list_files(self.tool, path="/", depth=1))
        self.assertEqual(result["items"][0]["type"], "directory")
        self.assertEqual(result["items"][1]["type"], "file")
        self.assertEqual(result["items"][1]["size"], 42)

    @patch("nova.tools.builtins.webdav._webdav_request", new_callable=AsyncMock)
    def test_stat_path_returns_single_entry(self, mocked_request):
        mocked_request.return_value = (
            207,
            """
            <d:multistatus xmlns:d=\"DAV:\">
              <d:response>
                <d:href>/remote.php/dav/files/alice/Documents/notes.txt</d:href>
                <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>42</d:getcontentlength></d:prop></d:propstat>
              </d:response>
            </d:multistatus>
            """,
        )

        result = asyncio.run(webdav.stat_path(self.tool, path="/notes.txt"))
        self.assertTrue(result["exists"])
        self.assertEqual(result["type"], "file")
        self.assertEqual(result["size"], 42)

    @patch("nova.tools.builtins.webdav._webdav_request", new_callable=AsyncMock)
    def test_create_folder_recursive_calls_mkcol_for_each_segment(self, mocked_request):
        mocked_request.return_value = (201, "")

        result = asyncio.run(webdav.create_folder(self.tool, "/a/b/c", recursive=True))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mocked_request.await_count, 3)

    @patch("nova.tools.builtins.webdav._webdav_request", new_callable=AsyncMock)
    def test_move_path_uses_destination_header(self, mocked_request):
        mocked_request.return_value = (201, "")

        result = asyncio.run(
            webdav.move_path(
                self.tool,
                source_path="/old.txt",
                destination_path="/archive/new.txt",
                overwrite=True,
            )
        )

        self.assertEqual(result["http_status"], 201)
        _, kwargs = mocked_request.await_args
        self.assertEqual(kwargs["headers"]["Overwrite"], "T")
        self.assertIn("/archive/new.txt", kwargs["headers"]["Destination"])

    @patch("nova.tools.builtins.webdav._webdav_request", new_callable=AsyncMock)
    def test_copy_path_uses_destination_header(self, mocked_request):
        mocked_request.return_value = (201, "")

        result = asyncio.run(
            webdav.copy_path(
                self.tool,
                source_path="/old.txt",
                destination_path="/archive/old.txt",
                overwrite=False,
            )
        )

        self.assertEqual(result["http_status"], 201)
        _, kwargs = mocked_request.await_args
        self.assertEqual(kwargs["headers"]["Overwrite"], "F")
        self.assertIn("/archive/old.txt", kwargs["headers"]["Destination"])

    def test_batch_move_paths_dry_run(self):
        result = asyncio.run(
            webdav.batch_move_paths(
                self.tool,
                operations=[
                    {"source_path": "/in/a.txt", "destination_path": "/out/a.txt"},
                    {"source_path": "/in/b.txt", "destination_path": "/out/b.txt"},
                ],
                dry_run=True,
            )
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["planned_count"], 2)

    @patch("nova.tools.builtins.webdav.move_path", new_callable=AsyncMock)
    def test_batch_move_paths_apply(self, mocked_move_path):
        mocked_move_path.return_value = {"status": "ok", "http_status": 204}

        result = asyncio.run(
            webdav.batch_move_paths(
                self.tool,
                operations=[{"source_path": "/in/a.txt", "destination_path": "/out/a.txt"}],
                dry_run=False,
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["error_count"], 0)

    @patch("nova.tools.builtins.webdav._get_webdav_config", new_callable=AsyncMock)
    def test_get_functions_only_exposes_enabled_mutations(self, mocked_config):
        mocked_config.return_value = {
            "allow_create_files": False,
            "allow_create_directories": True,
            "allow_move": True,
            "allow_copy": False,
            "allow_batch_move": True,
            "allow_delete": False,
        }

        tools = asyncio.run(webdav.get_functions(self.tool, agent=None))
        names = [tool.name for tool in tools]

        self.assertIn("webdav_list_files", names)
        self.assertIn("webdav_stat_path", names)
        self.assertIn("webdav_read_file", names)
        self.assertIn("webdav_create_folder", names)
        self.assertIn("webdav_move_path", names)
        self.assertIn("webdav_batch_move_paths", names)
        self.assertNotIn("webdav_write_file", names)
        self.assertNotIn("webdav_copy_path", names)
        self.assertNotIn("webdav_delete_path", names)

    @patch("nova.tools.builtins.webdav._get_webdav_config", new_callable=AsyncMock)
    def test_get_functions_exposes_all_mutations_when_enabled(self, mocked_config):
        mocked_config.return_value = {
            "allow_create_files": True,
            "allow_create_directories": True,
            "allow_move": True,
            "allow_copy": True,
            "allow_batch_move": True,
            "allow_delete": True,
        }

        tools = asyncio.run(webdav.get_functions(self.tool, agent=None))
        names = [tool.name for tool in tools]

        self.assertEqual(
            names,
            [
                "webdav_list_files",
                "webdav_stat_path",
                "webdav_read_file",
                "webdav_write_file",
                "webdav_create_folder",
                "webdav_move_path",
                "webdav_copy_path",
                "webdav_batch_move_paths",
                "webdav_delete_path",
            ],
        )

    def test_metadata_declares_skill_loading_permissions_and_instructions_exist(self):
        loading = webdav.METADATA.get("loading", {})
        self.assertEqual(loading.get("mode"), "skill")
        self.assertEqual(loading.get("skill_id"), "webdav")

        field_names = [field["name"] for field in webdav.METADATA.get("config_fields", [])]
        self.assertIn("allow_move", field_names)
        self.assertIn("allow_copy", field_names)
        self.assertIn("allow_batch_move", field_names)
        self.assertIn("allow_create_files", field_names)
        self.assertIn("allow_create_directories", field_names)
        self.assertIn("allow_delete", field_names)

        instructions = webdav.get_skill_instructions()
        self.assertIsInstance(instructions, list)
        self.assertTrue(instructions)
