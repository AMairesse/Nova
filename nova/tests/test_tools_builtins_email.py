from __future__ import annotations

import asyncio
import datetime as dt
import smtplib
from email.mime.text import MIMEText
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
        self.assertEqual(names.count("send_email"), 1)

    @patch("nova.tools.builtins.email.list_emails", new_callable=AsyncMock)
    def test_get_aggregated_functions_routes_calls_by_mailbox_alias(self, mocked_list_emails):
        second_tool = create_tool(
            self.user,
            name="Personal mailbox",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "bob@example.com",
                "password": "secret",
                "enable_sending": False,
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        mocked_list_emails.return_value = "ok"
        tools = asyncio.run(email_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))

        list_tool = next(tool for tool in tools if tool.name == "list_emails")
        first = asyncio.run(list_tool.coroutine(mailbox=self.tool.name, folder="INBOX", limit=2))
        second = asyncio.run(list_tool.coroutine(mailbox=second_tool.name, folder="INBOX", limit=3))

        self.assertEqual(first, "ok")
        self.assertEqual(second, "ok")
        self.assertEqual(mocked_list_emails.await_count, 2)
        self.assertEqual(mocked_list_emails.await_args_list[0].args[1], self.tool.id)
        self.assertEqual(mocked_list_emails.await_args_list[1].args[1], second_tool.id)

    def test_get_aggregated_functions_rejects_unknown_mailbox_alias(self):
        second_tool = create_tool(
            self.user,
            name="Personal mailbox",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "bob@example.com",
                "password": "secret",
                "enable_sending": False,
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        tools = asyncio.run(email_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))
        list_tool = next(tool for tool in tools if tool.name == "list_emails")

        result = asyncio.run(list_tool.coroutine(mailbox="unknown"))
        self.assertIn("Unknown mailbox", result)
        self.assertIn(self.tool.name, result)
        self.assertIn(second_tool.name, result)

    def test_get_aggregated_functions_blocks_send_when_sending_disabled(self):
        second_tool = create_tool(
            self.user,
            name="Personal mailbox",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "bob@example.com",
                "password": "secret",
                "enable_sending": False,
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        tools = asyncio.run(email_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))
        send_tool = next(tool for tool in tools if tool.name == "send_email")

        result = asyncio.run(
            send_tool.coroutine(
                mailbox=self.tool.name,
                to="bob@example.com",
                subject="Subject",
                body="Body",
            )
        )
        self.assertIn("Sending is disabled", result)

    def test_get_aggregated_prompt_instructions_include_mailbox_map(self):
        second_tool = create_tool(
            self.user,
            name="Personal mailbox",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "bob@example.com",
                "password": "secret",
                "enable_sending": True,
                "from_address": "mailbox@example.com",
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        hints = asyncio.run(
            email_tools.get_aggregated_prompt_instructions([self.tool, second_tool], agent=agent)
        )

        self.assertTrue(any(hint.startswith("Email mailbox map:") for hint in hints))
        self.assertTrue(any(self.tool.name in hint for hint in hints))
        self.assertTrue(any(second_tool.name in hint for hint in hints))

    def test_get_functions_keeps_legacy_names_for_single_email_tool(self):
        agent = SimpleNamespace(builtin_tools=[self.tool])

        tools = asyncio.run(email_tools.get_functions(self.tool, agent=agent))
        names = [tool.name for tool in tools]
        self.assertIn("list_emails", names)
        self.assertFalse(any(name.startswith("list_emails__") for name in names))

        list_tool = next(tool for tool in tools if tool.name == "list_emails")
        self.assertIn("[Mailbox:", list_tool.description)

    def test_decode_safe_get_capability_and_format_helpers(self):
        self.assertEqual(email_tools.decode_str("plain"), "plain")
        self.assertEqual(email_tools.decode_str(None), "")
        self.assertEqual(email_tools.safe_get({"A": 1, b"B": 2}, "A"), 1)
        self.assertEqual(email_tools.safe_get({"A": 1, b"B": 2}, b"B"), 2)
        self.assertTrue(email_tools.has_capability(SimpleNamespace(_server_capabilities=["MOVE"]), "move"))
        self.assertFalse(email_tools.has_capability(SimpleNamespace(_server_capabilities=[]), "move"))

        envelope = SimpleNamespace(
            sender="alice@example.com",
            subject="Subject",
            date=dt.datetime(2026, 2, 10, 8, 30),
        )
        formatted = email_tools.format_email_info(10, envelope)
        self.assertIn("ID: 10", formatted)
        self.assertIn("Subject: Subject", formatted)

    def test_metadata_groups_sending_controls_under_smtp_with_conditional_fields(self):
        fields_by_name = {
            item["name"]: item for item in email_tools.METADATA.get("config_fields", [])
        }
        loading_meta = email_tools.METADATA.get("loading", {})

        self.assertEqual(fields_by_name["enable_sending"]["group"], "smtp")
        self.assertIn("smtp_server", fields_by_name)
        self.assertIn("smtp_port", fields_by_name)
        self.assertIn("smtp_use_tls", fields_by_name)
        self.assertEqual(fields_by_name["smtp_server"]["visible_if"]["field"], "enable_sending")
        self.assertEqual(fields_by_name["smtp_port"]["visible_if"]["field"], "enable_sending")
        self.assertEqual(fields_by_name["smtp_use_tls"]["visible_if"]["field"], "enable_sending")
        self.assertEqual(loading_meta.get("mode"), "skill")
        self.assertEqual(loading_meta.get("skill_id"), "mail")

    def test_get_skill_instructions_returns_non_empty_list(self):
        instructions = email_tools.get_skill_instructions()

        self.assertIsInstance(instructions, list)
        self.assertTrue(instructions)

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_list_emails_returns_empty_and_formats_results(self, mocked_get_imap_client):
        client = Mock()
        mocked_get_imap_client.return_value = client
        client.search.return_value = []

        empty = asyncio.run(email_tools.list_emails(self.user, self.tool.id))
        self.assertIn("No emails found", empty)

        client.search.return_value = [1, 2]
        env = SimpleNamespace(sender="alice@example.com", subject="Hi", date=dt.datetime(2026, 2, 10, 9, 0))
        client.fetch.return_value = {2: {"ENVELOPE": env}, 1: {"ENVELOPE": env}}
        listed = asyncio.run(email_tools.list_emails(self.user, self.tool.id))
        self.assertIn("Recent emails in INBOX", listed)
        self.assertIn("ID: 2", listed)

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_read_email_handles_not_found_no_body_preview_and_full(self, mocked_get_imap_client):
        client = Mock()
        mocked_get_imap_client.return_value = client

        client.fetch.return_value = {}
        not_found = asyncio.run(email_tools.read_email(self.user, self.tool.id, message_id=99))
        self.assertIn("not found", not_found.lower())

        client.fetch.return_value = {1: {"ENVELOPE": SimpleNamespace(), "BODY[]": b""}}
        no_body = asyncio.run(email_tools.read_email(self.user, self.tool.id, message_id=1))
        self.assertIn("body not available", no_body.lower())

        body = MIMEText("A" * 600, "plain", "utf-8").as_bytes()
        envelope = SimpleNamespace(
            sender="alice@example.com",
            to=["bob@example.com"],
            subject="Long body",
            date=dt.datetime(2026, 2, 10, 9, 0),
        )
        client.fetch.return_value = {2: {"ENVELOPE": envelope, "BODY[]": body}}
        preview = asyncio.run(email_tools.read_email(self.user, self.tool.id, message_id=2, preview_only=True))
        self.assertIn("Content Preview", preview)
        self.assertIn("Content truncated", preview)

        full = asyncio.run(email_tools.read_email(self.user, self.tool.id, message_id=2, preview_only=False))
        self.assertIn("Full Content", full)

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_search_emails_and_mailboxes_and_capabilities(self, mocked_get_imap_client):
        client = Mock()
        mocked_get_imap_client.return_value = client

        client.search.side_effect = [[], []]
        no_match = asyncio.run(email_tools.search_emails(self.user, self.tool.id, query="xyz"))
        self.assertIn("No emails found matching", no_match)

        client.search.side_effect = [[4, 2], [3]]
        env = SimpleNamespace(sender="a", subject="b", date=dt.datetime(2026, 2, 10, 9, 0))
        client.fetch.return_value = {4: {"ENVELOPE": env}, 3: {"ENVELOPE": env}, 2: {"ENVELOPE": env}}
        matched = asyncio.run(email_tools.search_emails(self.user, self.tool.id, query="x", limit=2))
        self.assertIn("Emails matching", matched)

        client.list_folders.return_value = [([], "/", "INBOX"), "Archive"]
        folders = asyncio.run(email_tools.list_mailboxes(self.user, self.tool.id))
        self.assertIn("- INBOX", folders)
        self.assertIn("- Archive", folders)

        client._server_capabilities = ["MOVE", "UIDPLUS"]
        caps = asyncio.run(email_tools.get_server_capabilities(self.user, self.tool.id))
        self.assertIn("MOVE command: Yes", caps)
        self.assertIn("UIDPLUS: Yes", caps)

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    @patch("nova.tools.builtins.email.folder_exists", return_value=True)
    @patch("nova.tools.builtins.email.build_smtp_client")
    @patch("nova.tools.builtins.email.ToolCredential.objects.get")
    def test_send_email_uses_alt_sent_folder_and_handles_missing_smtp(
        self,
        mocked_get_credential,
        mocked_build_smtp_client,
        mocked_folder_exists,
        mocked_get_imap_client,
    ):
        credential = SimpleNamespace(
            config={
                "smtp_server": "smtp.example.com",
                "username": "alice@example.com",
                "password": "secret",
                "sent_folder": "Sent",
            }
        )
        mocked_get_credential.return_value = credential
        smtp_server = Mock()
        mocked_build_smtp_client.return_value = smtp_server
        imap_client = Mock()
        mocked_get_imap_client.return_value = imap_client

        sent = asyncio.run(
            email_tools.send_email(
                self.user,
                self.tool.id,
                to="bob@example.com",
                subject="Hello",
                body="Body",
                cc="carol@example.com",
            )
        )

        self.assertIn("Email sent successfully", sent)
        smtp_server.sendmail.assert_called_once()
        imap_client.append.assert_called_once()
        appended_folder = imap_client.append.call_args.args[0]
        self.assertEqual(appended_folder, "Sent")
        mocked_folder_exists.assert_called()

        credential.config["smtp_server"] = ""
        missing = asyncio.run(email_tools.send_email(self.user, self.tool.id, "bob@example.com", "x", "y"))
        self.assertIn("SMTP server not configured", missing)

    @patch("nova.tools.builtins.email.ToolCredential.objects.get", side_effect=email_tools.ToolCredential.DoesNotExist)
    def test_send_email_and_save_draft_handle_missing_credentials(self, mocked_get):
        sent = asyncio.run(email_tools.send_email(self.user, self.tool.id, "bob@example.com", "x", "y"))
        self.assertIn("No email credential found", sent)
        with self.assertRaisesMessage(ValueError, "No IMAP credential found"):
            asyncio.run(email_tools.save_draft(self.user, self.tool.id, "bob@example.com", "x", "y"))

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    @patch("nova.tools.builtins.email.folder_exists", return_value=False)
    def test_save_draft_handles_missing_folder(self, mocked_folder_exists, mocked_get_imap_client):
        mocked_get_imap_client.return_value = Mock()

        result = asyncio.run(
            email_tools.save_draft(
                self.user,
                self.tool.id,
                to="bob@example.com",
                subject="Draft",
                body="text",
                draft_folder="Drafts",
            )
        )

        self.assertIn("does not exist", result)
        mocked_folder_exists.assert_called_once()

    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    def test_mark_read_unread_and_delete_paths(self, mocked_get_imap_client):
        client = Mock()
        mocked_get_imap_client.return_value = client

        client.fetch.return_value = {}
        not_found = asyncio.run(email_tools.mark_email_as_read(self.user, self.tool.id, message_id=10))
        self.assertIn("not found", not_found.lower())

        client.fetch.return_value = {10: {"ENVELOPE": object()}}
        marked = asyncio.run(email_tools.mark_email_as_read(self.user, self.tool.id, message_id=10))
        self.assertIn("marked as read", marked)
        client.add_flags.assert_called_once()

        unread = asyncio.run(email_tools.mark_email_as_unread(self.user, self.tool.id, message_id=10))
        self.assertIn("marked as unread", unread)
        client.remove_flags.assert_called_once()

        deleted = asyncio.run(email_tools.delete_email(self.user, self.tool.id, message_id=10))
        self.assertIn("deleted", deleted)
        client.delete_messages.assert_called()

    @patch("nova.tools.builtins.email.ToolCredential.objects.get")
    @patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock)
    @patch("nova.tools.builtins.email.list_emails", new_callable=AsyncMock)
    def test_test_email_access_other_branches(self, mocked_list_emails, mocked_get_imap_client, mocked_get_credential):
        mocked_list_emails.return_value = "error: imap down"
        result = asyncio.run(email_tools.test_email_access(self.user, self.tool.id))
        self.assertEqual(result["status"], "error")

        mocked_list_emails.return_value = "ok"
        mocked_get_credential.return_value = SimpleNamespace(config={"enable_sending": False, "smtp_server": ""})
        imap_only = asyncio.run(email_tools.test_email_access(self.user, self.tool.id))
        self.assertEqual(imap_only["status"], "success")
        self.assertIn("IMAP connection successful", imap_only["message"])

        mocked_get_credential.return_value = SimpleNamespace(config={"enable_sending": True, "smtp_server": "smtp"})
        with patch("nova.tools.builtins.email.build_smtp_client", side_effect=smtplib.SMTPAuthenticationError(535, b"bad auth")):
            smtp_auth = asyncio.run(email_tools.test_email_access(self.user, self.tool.id))
        self.assertEqual(smtp_auth["status"], "partial")
        self.assertIn("SMTP: Authentication failed", smtp_auth["message"])

        with patch("nova.tools.builtins.email.build_smtp_client", return_value=Mock()):
            mocked_get_imap_client.return_value = Mock()
            with patch("nova.tools.builtins.email.folder_exists", side_effect=[False, False, False, False]):
                partial_sent = asyncio.run(email_tools.test_email_access(self.user, self.tool.id))
        self.assertEqual(partial_sent["status"], "partial")
        self.assertIn("no sent folder found", partial_sent["message"].lower())

    @patch("nova.tools.builtins.email.build_imap_client", side_effect=RuntimeError("boom"))
    @patch("nova.tools.builtins.email.ToolCredential.objects.get", return_value=SimpleNamespace(config={}))
    def test_get_imap_client_reraises_generic_errors(self, mocked_get_credential, mocked_build_imap):
        with self.assertRaisesMessage(RuntimeError, "boom"):
            asyncio.run(email_tools.get_imap_client(self.user, self.tool.id))

    @patch("nova.tools.builtins.email.ToolCredential.objects.get", side_effect=email_tools.ToolCredential.DoesNotExist)
    def test_get_imap_client_missing_credential(self, mocked_get_credential):
        with self.assertRaisesMessage(ValueError, "No IMAP credential found"):
            asyncio.run(email_tools.get_imap_client(self.user, self.tool.id))
