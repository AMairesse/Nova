from __future__ import annotations

import asyncio
import datetime as dt
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase

from nova.plugins.mail import service as mail_service
from nova.tests.factories import create_tool, create_tool_credential, create_user


def _address(name: str, email: str) -> SimpleNamespace:
    local, _, host = email.partition("@")
    return SimpleNamespace(name=name.encode("utf-8"), mailbox=local.encode("utf-8"), host=host.encode("utf-8"))


def _message_payload(subject: str = "Hello", body: str = "This is the email body.") -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Alice <alice@example.com>"
    message["To"] = "Bob <bob@example.com>"
    message.set_content(body)
    return message.as_bytes()


def _envelope(subject: str = "Hello") -> SimpleNamespace:
    return SimpleNamespace(
        sender=[_address("Alice", "alice@example.com")],
        to=[_address("Bob", "bob@example.com")],
        subject=subject.encode("utf-8"),
        date=dt.datetime(2026, 4, 12, 9, 30, tzinfo=dt.timezone.utc),
    )


class _FakeImapClient:
    def __init__(self, *, messages=None, mailboxes=None, capabilities=None):
        self.messages = dict(messages or {})
        self.mailboxes = list(mailboxes or [])
        self._capabilities = {str(item).upper() for item in (capabilities or set())}
        self.selected_folders: list[str] = []
        self.moved: list[tuple[list[int], str]] = []
        self.copied: list[tuple[list[int], str]] = []
        self.deleted: list[list[int]] = []
        self.expunged: list[list[int] | None] = []
        self.added_flags: list[tuple[list[int], list[str]]] = []
        self.removed_flags: list[tuple[list[int], list[str]]] = []
        self.logged_out = False

    def select_folder(self, folder):
        self.selected_folders.append(str(folder))
        return {"UIDVALIDITY": 1}

    def fetch(self, message_ids, _data):
        return {
            int(message_id): self.messages[int(message_id)]
            for message_id in message_ids
            if int(message_id) in self.messages
        }

    def search(self, _criteria):
        return sorted(self.messages.keys())

    def list_folders(self):
        return list(self.mailboxes)

    def has_capability(self, capability):
        return str(capability or "").upper() in self._capabilities

    def move(self, messages, folder):
        self.moved.append((list(messages), str(folder)))

    def copy(self, messages, folder):
        self.copied.append((list(messages), str(folder)))

    def delete_messages(self, messages, silent=False):
        del silent
        self.deleted.append(list(messages))

    def expunge(self, messages=None):
        self.expunged.append(list(messages) if messages is not None else None)

    def add_flags(self, messages, flags, silent=False):
        del silent
        self.added_flags.append((list(messages), list(flags)))

    def remove_flags(self, messages, flags, silent=False):
        del silent
        self.removed_flags.append((list(messages), list(flags)))

    def logout(self):
        self.logged_out = True


class MailServiceTests(TestCase):
    def setUp(self):
        self.user = create_user(username="mail-service", email="mail-service@example.com")
        self.tool = create_tool(
            self.user,
            name="Mailbox",
            tool_subtype="email",
            python_path="nova.plugins.mail",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "imap_server": "imap.example.com",
                "username": "alice@example.com",
                "password": "secret",
            },
        )

    def test_list_emails_includes_uid_and_flags(self):
        client = _FakeImapClient(
            messages={
                10: {
                    "ENVELOPE": _envelope("Invoice"),
                    "UID": 10,
                    "FLAGS": [b"\\Seen", b"\\Flagged"],
                }
            }
        )

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            result = asyncio.run(mail_service.list_emails(self.user, self.tool.id, folder="INBOX", limit=5))

        self.assertIn("UID: 10", result)
        self.assertIn("Flags: \\Seen, \\Flagged", result)
        self.assertIn("Alice <alice@example.com>", result)

    def test_read_email_supports_uid_and_includes_flags(self):
        client = _FakeImapClient(
            messages={
                10: {
                    "ENVELOPE": _envelope("Project update"),
                    "UID": 10,
                    "FLAGS": [b"\\Seen"],
                    "BODY[]": _message_payload("Project update", "Everything is on track."),
                }
            }
        )

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            result = asyncio.run(
                mail_service.read_email(
                    self.user,
                    self.tool.id,
                    uid=10,
                    folder="INBOX",
                    preview_only=True,
                )
            )

        self.assertIn("UID: 10", result)
        self.assertIn("Flags: \\Seen", result)
        self.assertIn("Everything is on track.", result)

    def test_list_mailboxes_exposes_special_use_and_flags(self):
        client = _FakeImapClient(
            mailboxes=[
                ((b"\\HasNoChildren", b"\\Junk"), "/", "Spam"),
                ((b"\\HasNoChildren",), "/", "Archive"),
            ]
        )

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            result = asyncio.run(mail_service.list_mailboxes(self.user, self.tool.id))

        self.assertIn("- Spam [special: junk; flags: \\HasNoChildren, \\Junk]", result)
        self.assertIn("- Archive [special: archive; flags: \\HasNoChildren]", result)

    def test_move_emails_uses_move_when_supported_and_resolves_special_mailbox(self):
        client = _FakeImapClient(
            mailboxes=[((b"\\Junk",), "/", "Spam")],
            capabilities={"MOVE"},
        )

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            result = asyncio.run(
                mail_service.move_emails(
                    self.user,
                    self.tool.id,
                    uids=[10, 11],
                    source_folder="INBOX",
                    target_special="junk",
                )
            )

        self.assertEqual(client.moved, [([10, 11], "Spam")])
        self.assertIn("Moved 2 email(s) from INBOX to Spam.", result)

    def test_move_emails_falls_back_to_copy_delete_expunge(self):
        client = _FakeImapClient(mailboxes=[((), "/", "Archive")], capabilities=set())

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            result = asyncio.run(
                mail_service.move_emails(
                    self.user,
                    self.tool.id,
                    message_ids=[42],
                    source_folder="INBOX",
                    target_folder="Archive",
                )
            )

        self.assertEqual(client.copied, [([42], "Archive")])
        self.assertEqual(client.deleted, [[42]])
        self.assertEqual(client.expunged, [[42]])
        self.assertIn("Moved 1 email(s) from INBOX to Archive.", result)

    def test_mark_emails_updates_imap_flags(self):
        client = _FakeImapClient()

        with patch("nova.plugins.mail.service.get_imap_client", new=AsyncMock(return_value=client)):
            seen_result = asyncio.run(
                mail_service.mark_emails(
                    self.user,
                    self.tool.id,
                    uids=[7],
                    folder="INBOX",
                    action="seen",
                )
            )
            unflagged_result = asyncio.run(
                mail_service.mark_emails(
                    self.user,
                    self.tool.id,
                    message_ids=[8],
                    folder="INBOX",
                    action="unflagged",
                )
            )

        self.assertEqual(client.added_flags, [([7], ["\\Seen"])])
        self.assertEqual(client.removed_flags, [([8], ["\\Flagged"])])
        self.assertIn("Marked 1 email(s) in INBOX as seen.", seen_result)
        self.assertIn("Marked 1 email(s) in INBOX as unflagged.", unflagged_result)
