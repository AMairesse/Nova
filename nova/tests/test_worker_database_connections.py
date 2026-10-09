"""Connection lifecycle checks; external IMAP/LLM services are simulated."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync, sync_to_async
from django.db import OperationalError, connection, connections
from django.test import SimpleTestCase, TransactionTestCase

from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.tasks.TaskExecutor import TaskErrorCategory, TaskExecutor
from nova.tasks.execution_trace import TaskExecutionTraceHandler
from nova.tasks.tasks import execute_agent_task_with_executor, poll_task_definition_email
from nova.tests.factories import create_agent, create_provider, create_tool, create_tool_credential, create_user


class WorkerDatabaseBoundaryTests(SimpleTestCase):
    def test_agent_orm_uses_the_thread_managed_by_celery(self):
        owner_thread = threading.get_ident()
        observed = []

        async def execute():
            observed.append(await sync_to_async(threading.get_ident, thread_sensitive=True)())

        with patch("nova.tasks.tasks.ReactTerminalTaskExecutor") as executor:
            executor.return_value.execute_or_resume = execute
            execute_agent_task_with_executor(None, None, None, None, "test")
        self.assertEqual(observed, [owner_thread])

    def test_trace_cleans_connections_in_the_thread_that_persists_it(self):
        cleaned_in = []
        persisted_in = []
        handler = TaskExecutionTraceHandler(SimpleNamespace(id=1, execution_trace={}))
        with (
            patch("channels.db.close_old_connections", side_effect=lambda: cleaned_in.append(threading.get_ident())),
            patch("nova.models.Task.Task.objects.filter") as query,
        ):
            query.return_value.update.side_effect = lambda **kwargs: persisted_in.append(threading.get_ident())
            async_to_sync(handler.ensure_root_run)(label="test")
        self.assertEqual(cleaned_in, persisted_in * 2)

    def test_database_connection_error_is_not_classified_as_mail_network_error(self):
        executor = TaskExecutor.__new__(TaskExecutor)
        self.assertEqual(executor._categorize_error(OperationalError("the connection is closed")), TaskErrorCategory.SYSTEM_ERROR)


class AntispamDatabaseLifecycleTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="antispam-connection-test")
        self.provider = create_provider(self.user, provider_type="openai", model="gpt-4.1-mini")
        self.agent = create_agent(self.user, self.provider)
        self.tool = create_tool(self.user, name="Test mailbox", tool_subtype="email")
        create_tool_credential(self.user, self.tool, config={"imap_server": "imap.invalid", "username": "test@example.invalid", "password": "test-only"})
        self.agent.tools.add(self.tool)
        self.definition = TaskDefinition.objects.create(
            user=self.user, agent=self.agent, email_tool=self.tool,
            name="Antispam test", task_kind=TaskDefinition.TaskKind.AGENT,
            trigger_type=TaskDefinition.TriggerType.EMAIL_POLL,
            run_mode=TaskDefinition.RunMode.NEW_THREAD, is_active=True,
            prompt="Filter {{ new_emails_json }}. Move spam to junk.",
            last_error="old agent failure",
        )
        self.inbox = {42}
        self.junk = set()
        self.client = Mock()
        self.client.select_folder.return_value = {"UIDVALIDITY": 1}
        self.client.search.return_value = [42]
        self.envelopes = {42: {"ENVELOPE": SimpleNamespace(subject=b"Test spam", sender=[], date=None)}}
        self.client.fetch.return_value = self.envelopes
        self.client.list_folders.return_value = [((b"\\Junk",), b"/", "Junk")]
        self.client.has_capability.return_value = True
        self.client.move.side_effect = self._move

    def _move(self, uids, folder):
        self.assertEqual(folder, "Junk")
        for uid in uids:
            self.inbox.remove(uid)
            self.junk.add(uid)

    def _poll(self, *, failure_after_move=False, disconnect_during_agent=False):
        replies = [
            {"content": "", "tool_calls": [{"id": "move-spam", "name": "terminal", "arguments": json.dumps({"command": "mail move --uid 42 --to-special junk"})}]},
            OperationalError("the connection is closed") if failure_after_move else {"content": "Spam moved to Junk.", "tool_calls": [], "total_tokens": 10},
        ]
        async def next_reply(*args, **kwargs):
            if disconnect_during_agent and len(replies) == 2:
                await sync_to_async(self._terminate_postgres_connection, thread_sensitive=True)()
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with (
            patch("nova.tasks.email_polling.build_imap_client", return_value=self.client),
            patch("nova.plugins.mail.service.build_imap_client", return_value=self.client),
            patch("nova.runtime.provider_client.ProviderClient.stream_chat_completion", new=AsyncMock(side_effect=next_reply)),
            patch("nova.tasks.tasks.schedule_trigger_task_retry") as retry,
        ):
            result = poll_task_definition_email.run(self.definition.pk)
        retry.assert_not_called()
        return result

    def test_complete_antispam_run_fetches_headers_executes_agent_and_moves_once(self):
        self._assert_complete_run(self._poll())

    def _assert_complete_run(self, result):
        self.assertEqual(result["status"], "ok")
        self.client.fetch.assert_called_once_with([42], ["ENVELOPE"])
        self.client.move.assert_called_once_with([42], "Junk")
        self.assertEqual((self.inbox, self.junk), (set(), {42}))
        run = RoutineRun.objects.get(definition=self.definition)
        self.assertEqual(run.status, TaskStatus.COMPLETED)
        self.assertTrue(run.task.execution_trace["root"]["children"])
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.runtime_state["last_uid"], 42)
        self.assertIsNone(self.definition.last_error)

    def test_database_error_after_move_does_not_repeat_the_external_action(self):
        with self.assertLogs("nova.tasks.TaskExecutor", level="ERROR"):
            result = self._poll(failure_after_move=True)
        self.assertEqual(result["status"], "failed")
        self.client.move.assert_called_once_with([42], "Junk")
        self.definition.refresh_from_db()
        self.assertFalse(self.definition.is_active)
        self.assertNotIn("last_uid", self.definition.runtime_state)
        self.assertEqual(poll_task_definition_email.run(self.definition.pk)["reason"], "inactive")
        self.client.move.assert_called_once()

    @skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL test settings")
    def test_agent_succeeds_with_a_closed_connection_in_the_old_asgiref_pool(self):
        def poison_old_pool():
            connection.ensure_connection()
            connection.connection.close()  # Close psycopg without resetting Django's wrapper.

        asyncio.run(sync_to_async(poison_old_pool, thread_sensitive=True)())
        try:
            # Negative control: the previous plain bridge really reuses the
            # closed psycopg handle instead of transparently reconnecting.
            with self.assertRaisesMessage(OperationalError, "the connection is closed"):
                asyncio.run(sync_to_async(Task.objects.count, thread_sensitive=True)())
            self.test_complete_antispam_run_fetches_headers_executes_agent_and_moves_once()
        finally:
            asyncio.run(sync_to_async(connections.close_all, thread_sensitive=True)())

    @skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL test settings")
    def test_poll_renews_postgres_connection_terminated_during_imap_fetch(self):
        def fetch_after_disconnect(*args):
            self._terminate_postgres_connection()
            return self.envelopes

        self.client.fetch.side_effect = fetch_after_disconnect
        self.test_complete_antispam_run_fetches_headers_executes_agent_and_moves_once()

    @skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL test settings")
    def test_agent_renews_postgres_connection_terminated_during_provider_call(self):
        self._assert_complete_run(self._poll(disconnect_during_agent=True))

    @staticmethod
    def _terminate_postgres_connection():
        import psycopg
        connection.ensure_connection()
        pid = connection.connection.info.backend_pid
        with psycopg.connect(**connection.get_connection_params()) as control:
            control.execute("SELECT pg_terminate_backend(%s)", [pid])
