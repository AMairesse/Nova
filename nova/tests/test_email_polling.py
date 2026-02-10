from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from nova.models.TaskDefinition import TaskDefinition
from nova.tasks.email_polling import poll_new_unseen_email_headers
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)


class EmailPollingTests(TestCase):
    def setUp(self):
        self._task_counter = 0
        self.user = create_user(username="poll-user", email="poll@example.com")
        self.provider = create_provider(self.user, name="poll-provider")
        self.agent = create_agent(self.user, self.provider, name="poll-agent")
        self.email_tool = create_tool(
            self.user,
            name="Email tool",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            self.email_tool,
            config={
                "imap_server": "imap.example.com",
                "username": "alice@example.com",
                "password": "secret",
            },
        )

    def _task(self, **kwargs):
        self._task_counter += 1
        defaults = {
            "user": self.user,
            "name": f"Email Poll Task {self._task_counter}",
            "task_kind": TaskDefinition.TaskKind.AGENT,
            "trigger_type": TaskDefinition.TriggerType.EMAIL_POLL,
            "agent": self.agent,
            "prompt": "Poll inbox",
            "run_mode": TaskDefinition.RunMode.NEW_THREAD,
            "email_tool": self.email_tool,
            "poll_interval_minutes": 5,
            "timezone": "UTC",
            "is_active": False,
            "runtime_state": {},
        }
        defaults.update(kwargs)
        return TaskDefinition.objects.create(**defaults)

    def test_poll_requires_email_trigger_and_tool(self):
        wrong_trigger = self._task(trigger_type=TaskDefinition.TriggerType.CRON)
        with self.assertRaisesMessage(ValueError, "requires an email polling task definition"):
            poll_new_unseen_email_headers(wrong_trigger)

        missing_tool = self._task(email_tool=None)
        with self.assertRaisesMessage(ValueError, "Email tool is required"):
            poll_new_unseen_email_headers(missing_tool)

    def test_poll_raises_when_credential_missing(self):
        task = self._task(email_tool=create_tool(self.user, name="other-email", tool_subtype="email"))
        with self.assertRaisesMessage(ValueError, "No credential found for selected email tool."):
            poll_new_unseen_email_headers(task)

    @patch("nova.tasks.email_polling.safe_imap_logout")
    @patch("nova.tasks.email_polling.build_imap_client")
    def test_poll_returns_new_headers_and_updates_state(self, mocked_build_imap, mocked_logout):
        task = self._task()
        client = Mock()
        mocked_build_imap.return_value = client
        client.select_folder.return_value = {"UIDVALIDITY": 42}
        client.search.return_value = [10, 11]
        env1 = SimpleNamespace(
            sender=[SimpleNamespace(name=b"Alice", mailbox=b"alice", host=b"example.com")],
            subject=b"Hello",
            date=dt.datetime(2026, 2, 1, 10, 0, tzinfo=dt.timezone.utc),
        )
        env2 = SimpleNamespace(
            from_=[SimpleNamespace(name="Bob", mailbox="bob", host="example.com")],
            subject="Update",
            date=dt.datetime(2026, 2, 1, 10, 5, tzinfo=dt.timezone.utc),
        )
        client.fetch.return_value = {
            10: {"ENVELOPE": env1},
            11: {"ENVELOPE": env2},
        }

        result = poll_new_unseen_email_headers(task)

        self.assertEqual(result["skip_reason"], None)
        self.assertEqual([h["uid"] for h in result["headers"]], [10, 11])
        self.assertEqual(result["state"]["uidvalidity"], 42)
        self.assertEqual(result["state"]["last_uid"], 11)
        mocked_logout.assert_called_once_with(client)

    @patch("nova.tasks.email_polling.safe_imap_logout")
    @patch("nova.tasks.email_polling.build_imap_client")
    def test_poll_skips_backlog_after_long_downtime(self, mocked_build_imap, mocked_logout):
        now = timezone.now()
        task = self._task(
            runtime_state={
                "last_poll_at": (now - dt.timedelta(minutes=30)).isoformat(),
                "last_uid": 4,
                "uidvalidity": 1,
            }
        )
        client = Mock()
        mocked_build_imap.return_value = client
        client.select_folder.return_value = {"UIDVALIDITY": 1}
        client.search.return_value = [5, 6, 7]

        result = poll_new_unseen_email_headers(task)

        self.assertEqual(result["headers"], [])
        self.assertEqual(result["skip_reason"], "backlog_skipped")
        self.assertEqual(result["state"]["last_uid"], 7)
        self.assertIn("backlog_skipped_at", result["state"])
        client.fetch.assert_not_called()
        mocked_logout.assert_called_once_with(client)

    @patch("nova.tasks.email_polling.build_imap_client")
    def test_poll_resets_cursor_when_uidvalidity_changes(self, mocked_build_imap):
        task = self._task(
            runtime_state={
                "uidvalidity": 999,
                "last_uid": 500,
            }
        )
        client = Mock()
        mocked_build_imap.return_value = client
        client.select_folder.return_value = {"UIDVALIDITY": 1000}
        client.search.return_value = [20]
        env = SimpleNamespace(
            sender=[SimpleNamespace(name="Carol", mailbox="carol", host="example.com")],
            subject="Reset cursor",
            date=dt.datetime(2026, 2, 1, 12, 0, tzinfo=dt.timezone.utc),
        )
        client.fetch.return_value = {20: {"ENVELOPE": env}}

        result = poll_new_unseen_email_headers(task)

        self.assertEqual(len(result["headers"]), 1)
        self.assertEqual(result["headers"][0]["uid"], 20)
        self.assertEqual(result["state"]["last_uid"], 20)
