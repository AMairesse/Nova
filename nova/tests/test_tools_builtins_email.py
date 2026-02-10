from __future__ import annotations

import asyncio
import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import TransactionTestCase

from nova.models.Tool import Tool
from nova.tests.factories import create_tool, create_tool_credential, create_user
from nova.tools.builtins import email as email_tools


class EmailBuiltinsTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="email-tool-user", email="email-tool@example.com")
        self.tool = create_tool(
            self.user,
            name="Email tool",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "imap_server": "imap.example.com",
                "username": "alice@example.com",
                "password": "secret",
                "enable_sending": False,
            },
        )

    def test_build_imap_client_requires_config(self):
        credential = SimpleNamespace(config={"imap_server": "imap.example.com", "username": "x"})
        with self.assertRaisesMessage(ValueError, "Incomplete IMAP configuration"):
            email_tools.build_imap_client(credential)

    def test_folder_exists_handles_tuples_and_errors(self):
        client = Mock()
        client.list_folders.return_value = [([], b"/", "INBOX"), "Archive"]
        self.assertTrue(email_tools.folder_exists(client, "INBOX"))

        client.list_folders.side_effect = RuntimeError("boom")
        self.assertFalse(email_tools.folder_exists(client, "INBOX"))

    @patch("nova.tools.builtins.email.has_capability", return_value=True)
    @patch("nova.tools.builtins.email.folder_exists", side_effect=[True, True])
    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_move_email_to_folder_uses_move_when_supported(
        self,
        mocked_get_client,
        mocked_folder_exists,
        mocked_has_capability,
    ):
        client = Mock()
        client.fetch.return_value = {12: {"ENVELOPE": object()}}
        mocked_get_client.return_value = client

        result = asyncio.run(
            email_tools.move_email_to_folder(
                self.user,
                self.tool.id,
                message_id=12,
                source_folder="INBOX",
                target_folder="Junk",
            )
        )

        self.assertIn("moved from INBOX to Junk", result)
        client.move.assert_called_once_with([12], "Junk")
        self.assertEqual(mocked_folder_exists.call_count, 2)
        mocked_has_capability.assert_called_once_with(client, "MOVE")

    @patch("nova.tools.builtins.email.has_capability", return_value=False)
    @patch("nova.tools.builtins.email.folder_exists", side_effect=[True, True])
    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_move_email_to_folder_returns_error_when_fallback_fails(
        self,
        mocked_get_client,
        mocked_folder_exists,
        mocked_has_capability,
    ):
        client = Mock()
        client.fetch.return_value = {12: {"ENVELOPE": object()}}
        client.copy.side_effect = RuntimeError("copy failed")
        mocked_get_client.return_value = client

        result = asyncio.run(
            email_tools.move_email_to_folder(
                self.user,
                self.tool.id,
                message_id=12,
            )
        )

        self.assertIn("Error moving email with fallback method", result)
        self.assertIn("copy failed", result)
        mocked_has_capability.assert_called_once()
        self.assertEqual(mocked_folder_exists.call_count, 2)

    @patch("nova.tools.builtins.email.list_emails", new_callable=AsyncMock, return_value="ok")
    @patch("nova.tools.builtins.email.build_smtp_client", side_effect=smtplib.SMTPConnectError(421, "down"))
    @patch("nova.tools.builtins.email.ToolCredential.objects.get")
    def test_test_email_access_reports_partial_on_smtp_connection_error(
        self,
        mocked_get_cred,
        mocked_build_smtp,
        mocked_list_emails,
    ):
        mocked_get_cred.return_value = SimpleNamespace(
            config={
                "enable_sending": True,
                "smtp_server": "smtp.example.com",
            }
        )

        result = asyncio.run(email_tools.test_email_access(self.user, self.tool.id))

        self.assertEqual(result["status"], "partial")
        self.assertIn("SMTP: Connection failed", result["message"])
        mocked_list_emails.assert_awaited_once()
        mocked_build_smtp.assert_called_once()

    def test_get_functions_validates_required_tool_data(self):
        invalid_tool = Tool(
            user=self.user,
            name="invalid",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        with self.assertRaisesMessage(ValueError, "missing required data"):
            asyncio.run(email_tools.get_functions(invalid_tool, agent=None))

    def test_get_functions_returns_expected_toolset(self):
        tools = asyncio.run(email_tools.get_functions(self.tool, agent=None))
        names = [tool.name for tool in tools]

        self.assertIn("list_emails", names)
        self.assertIn("read_email", names)
        self.assertIn("send_email", names)
        self.assertIn("save_draft", names)
