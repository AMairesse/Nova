import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from nova.models.APIToolOperation import APIToolOperation
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import TerminalCommandError

from .runtime_command_base import TerminalExecutorCommandTestCase


class IntegrationCommandTests(TerminalExecutorCommandTestCase):
    def test_mcp_commands_support_schema_refresh_call_and_extract_output(self):
        mcp_tool = self._create_mcp_tool()
        executor = self._build_executor(
            TerminalCapabilities(mcp_tools=[mcp_tool])
        )
        async_to_sync(executor.vfs.write_file)(
            "/tmp/input.json",
            b'{"query":"roadmap"}',
            mime_type="application/json",
        )

        discovered_tools = [
            {
                "name": "list_pages",
                "description": "List pages",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ]
        schema_payload = {
            "server": {"id": mcp_tool.id, "name": mcp_tool.name, "endpoint": mcp_tool.endpoint},
            "tool": {
                "name": "list_pages",
                "description": "List pages",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                "output_schema": {"type": "object"},
            },
        }
        call_payload = {
            "payload": {
                "server": {"id": mcp_tool.id, "name": mcp_tool.name, "endpoint": mcp_tool.endpoint},
                "tool": {"name": "export_report", "description": "Export report"},
                "input": {"query": "roadmap"},
                "result": {"report": "ready"},
            },
            "extractable_artifacts": [
                SimpleNamespace(
                    path="report.txt",
                    content=b"report ready",
                    mime_type="text/plain",
                )
            ],
        }

        with (
            patch(
                "nova.runtime.terminal.mcp_service.list_mcp_tools",
                new_callable=AsyncMock,
                return_value=discovered_tools,
            ) as mocked_list,
            patch(
                "nova.runtime.terminal.mcp_service.describe_mcp_tool",
                new_callable=AsyncMock,
                return_value=schema_payload,
            ) as mocked_schema,
            patch(
                "nova.runtime.terminal.mcp_service.call_mcp_tool",
                new_callable=AsyncMock,
                return_value=call_payload,
            ) as mocked_call,
        ):
            servers = async_to_sync(executor.execute)("mcp servers")
            tools = async_to_sync(executor.execute)('mcp tools --server "Notion MCP"')
            schema_written = async_to_sync(executor.execute)(
                'mcp schema list_pages --server "Notion MCP" > /tmp/mcp-schema.json'
            )
            called = async_to_sync(executor.execute)(
                'mcp call export_report --server "Notion MCP" --input-file /tmp/input.json '
                '--extract-to /reports --output /tmp/mcp-result.json'
            )
            refreshed = async_to_sync(executor.execute)('mcp refresh --server "Notion MCP"')

        self.assertIn("Notion MCP", servers)
        self.assertIn("list_pages", tools)
        self.assertIn("/tmp/mcp-schema.json", schema_written)
        self.assertIn("/tmp/mcp-result.json", called)
        self.assertIn("/reports/report.txt", called)
        self.assertIn("Refreshed Notion MCP", refreshed)
        self.assertEqual(
            json.loads(async_to_sync(executor.execute)("cat /tmp/mcp-schema.json"))["tool"]["name"],
            "list_pages",
        )
        self.assertEqual(
            json.loads(async_to_sync(executor.execute)("cat /tmp/mcp-result.json"))["result"]["report"],
            "ready",
        )
        self.assertEqual(
            async_to_sync(executor.execute)("cat /reports/report.txt"),
            "report ready",
        )
        self.assertEqual(mocked_call.await_args.kwargs["payload"], {"query": "roadmap"})
        self.assertEqual(mocked_schema.await_args.kwargs["tool_name"], "list_pages")
        self.assertTrue(any(call.kwargs.get("force_refresh") for call in mocked_list.await_args_list))

    def test_api_commands_support_schema_pipes_and_redirected_calls(self):
        api_tool = self._create_api_tool()
        self._create_api_operation(
            api_tool,
            name="Create invoice",
            slug="create_invoice",
            http_method=APIToolOperation.HTTPMethod.POST,
            path_template="/invoices/{invoice_id}",
            query_parameters=["mode"],
            body_parameter="payload",
        )
        executor = self._build_executor(
            TerminalCapabilities(api_tools=[api_tool])
        )
        async_to_sync(executor.vfs.write_file)(
            "/tmp/payload.json",
            b'{"invoice_id":42,"mode":"draft","payload":{"amount":199}}',
            mime_type="application/json",
        )

        operations_payload = [
            {
                "id": 1,
                "name": "Create invoice",
                "slug": "create_invoice",
                "description": "Create invoice",
                "http_method": "POST",
                "path_template": "/invoices/{invoice_id}",
            }
        ]
        schema_payload = {
            "service": {"id": api_tool.id, "name": api_tool.name, "endpoint": api_tool.endpoint},
            "operation": {
                "id": 1,
                "name": "Create invoice",
                "slug": "create_invoice",
                "description": "Create invoice",
                "http_method": "POST",
                "path_template": "/invoices/{invoice_id}",
                "query_parameters": ["mode"],
                "body_parameter": "payload",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        }
        call_result = {
            "payload": {
                "service": {"id": api_tool.id, "name": api_tool.name, "endpoint": api_tool.endpoint},
                "operation": {
                    "id": 1,
                    "name": "Create invoice",
                    "slug": "create_invoice",
                    "http_method": "POST",
                    "path_template": "/invoices/{invoice_id}",
                },
                "request": {
                    "url": "https://api.example.com/invoices/42?mode=draft",
                    "method": "POST",
                    "query": {"mode": "draft"},
                    "body": {"amount": 199},
                },
                "response": {
                    "status_code": 200,
                    "content_type": "application/json",
                    "headers": {"content-type": "application/json"},
                    "body_kind": "json",
                    "json": {"ok": True, "invoice_id": 42},
                    "text": "{\"ok\": true, \"invoice_id\": 42}",
                    "size": 31,
                    "filename": "response.json",
                },
            },
            "body_kind": "json",
            "binary_content": b'{"ok": true, "invoice_id": 42}',
            "filename": "response.json",
            "content_type": "application/json",
        }

        with (
            patch(
                "nova.runtime.terminal.api_tools_service.list_api_operations",
                new_callable=AsyncMock,
                return_value=operations_payload,
            ),
            patch(
                "nova.runtime.terminal.api_tools_service.describe_api_operation",
                new_callable=AsyncMock,
                return_value=schema_payload,
            ) as mocked_schema,
            patch(
                "nova.runtime.terminal.api_tools_service.call_api_operation",
                new_callable=AsyncMock,
                return_value=call_result,
            ) as mocked_call,
        ):
            services = async_to_sync(executor.execute)("api services")
            filtered = async_to_sync(executor.execute)('api operations --service "CRM API" | grep create_invoice')
            schema_written = async_to_sync(executor.execute)(
                'api schema create_invoice --service "CRM API" > /tmp/api-schema.json'
            )
            call_written = async_to_sync(executor.execute)(
                'api call create_invoice --service "CRM API" < /tmp/payload.json > /tmp/api-result.json'
            )

        self.assertIn("CRM API", services)
        self.assertIn("create_invoice", filtered)
        self.assertIn("/tmp/api-schema.json", schema_written)
        self.assertIn("/tmp/api-result.json", call_written)
        self.assertEqual(
            json.loads(async_to_sync(executor.execute)("cat /tmp/api-schema.json"))["operation"]["slug"],
            "create_invoice",
        )
        self.assertTrue(
            json.loads(async_to_sync(executor.execute)("cat /tmp/api-result.json"))["response"]["json"]["ok"]
        )
        self.assertEqual(
            mocked_call.await_args.kwargs["payload"],
            {"invoice_id": 42, "mode": "draft", "payload": {"amount": 199}},
        )
        self.assertEqual(mocked_schema.await_args.kwargs["operation_selector"], "create_invoice")

    def test_api_call_rejects_binary_shell_redirection_without_output(self):
        api_tool = self._create_api_tool()
        self._create_api_operation(
            api_tool,
            name="Export PDF",
            slug="export_pdf",
            http_method=APIToolOperation.HTTPMethod.GET,
            path_template="/invoices/{invoice_id}/pdf",
        )
        executor = self._build_executor(
            TerminalCapabilities(api_tools=[api_tool])
        )

        with patch(
            "nova.runtime.terminal.api_tools_service.call_api_operation",
            new_callable=AsyncMock,
            return_value={
                "payload": {
                    "service": {"id": api_tool.id, "name": api_tool.name, "endpoint": api_tool.endpoint},
                    "operation": {"slug": "export_pdf", "http_method": "GET"},
                    "request": {"url": "https://api.example.com/invoices/42/pdf", "method": "GET"},
                    "response": {
                        "status_code": 200,
                        "content_type": "application/pdf",
                        "headers": {"content-type": "application/pdf"},
                        "body_kind": "binary",
                        "json": None,
                        "text": None,
                        "size": 7,
                        "filename": "invoice.pdf",
                    },
                },
                "body_kind": "binary",
                "binary_content": b"%PDF...",
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
            },
        ):
            with self.assertRaises(TerminalCommandError) as cm:
                async_to_sync(executor.execute)(
                    'api call export_pdf --service "CRM API" invoice_id=42 > /tmp/invoice.bin'
                )

        self.assertIn("cannot be piped or redirected", str(cm.exception))

    def test_mcp_and_api_require_explicit_selector_when_multiple_services_exist(self):
        first_mcp = self._create_mcp_tool(name="Notion MCP")
        second_mcp = self._create_mcp_tool(name="Drive MCP", endpoint="https://mcp-drive.example.com")
        first_api = self._create_api_tool(name="CRM API")
        second_api = self._create_api_tool(name="Billing API", endpoint="https://billing.example.com")
        executor = self._build_executor(
            TerminalCapabilities(
                mcp_tools=[first_mcp, second_mcp],
                api_tools=[first_api, second_api],
            )
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mcp tools")
        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("api operations")

    def test_mail_accounts_and_multi_mailbox_selection_are_explicit(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        personal_tool = self._create_email_tool(name="Personal Mail", address="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool, personal_tool])
        )

        accounts = async_to_sync(executor.execute)("mail accounts")
        self.assertIn("work@example.com", accounts)
        self.assertIn("personal@example.com", accounts)

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mail list")

        with patch("nova.plugins.mail.service.list_emails", new_callable=AsyncMock, return_value="ok") as mocked_list:
            listed = async_to_sync(executor.execute)("mail list --mailbox personal@example.com --limit 5")

        self.assertEqual(listed, "ok")
        mocked_list.assert_awaited_once_with(self.user, personal_tool.id, folder="INBOX", limit=5)

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mail list --mailbox missing@example.com")

    def test_mail_rejects_ambiguous_mailbox_identifiers(self):
        first_tool = self._create_email_tool(
            name="Shared Mail A",
            address="shared@example.com",
            imap_server="imap.example.com",
        )
        second_tool = self._create_email_tool(
            name="Shared Mail B",
            address="shared@example.com",
            imap_server="imap.example.com",
        )
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[first_tool, second_tool])
        )

        with self.assertRaises(TerminalCommandError) as cm:
            async_to_sync(executor.execute)("mail list --mailbox shared@example.com")

        self.assertIn("Ambiguous mailbox", str(cm.exception))

    def test_single_mailbox_allows_mail_commands_without_mailbox_flag(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool])
        )

        with patch("nova.plugins.mail.service.list_emails", new_callable=AsyncMock, return_value="ok") as mocked_list:
            listed = async_to_sync(executor.execute)("mail list --limit 3")

        self.assertEqual(listed, "ok")
        mocked_list.assert_awaited_once_with(self.user, work_tool.id, folder="INBOX", limit=3)

    def test_mail_send_uses_selected_mailbox(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        personal_tool = self._create_email_tool(name="Personal Mail", address="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool, personal_tool])
        )
        async_to_sync(executor.vfs.write_file)(
            "/body.txt",
            b"Hello from Nova",
            mime_type="text/plain",
        )

        with patch.object(executor, "_send_mail_direct", new=AsyncMock(return_value="sent")) as mocked_send:
            result = async_to_sync(executor.execute)(
                "mail send --mailbox personal@example.com --to bob@example.com "
                '--subject "Hello" --body-file /body.txt'
            )

        self.assertEqual(result, "sent")
        mocked_send.assert_awaited_once()
        self.assertEqual(mocked_send.await_args.kwargs["tool_id"], personal_tool.id)

    def test_mail_read_accepts_uid_selector(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        executor = self._build_executor(TerminalCapabilities(email_tools=[work_tool]))

        with patch("nova.plugins.mail.service.read_email", new_callable=AsyncMock, return_value="message") as mocked_read:
            result = async_to_sync(executor.execute)("mail read --uid 42 --full")

        self.assertEqual(result, "message")
        mocked_read.assert_awaited_once_with(
            self.user,
            work_tool.id,
            None,
            uid=42,
            folder="INBOX",
            preview_only=False,
        )

    def test_mail_move_and_mark_forward_selectors(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        executor = self._build_executor(TerminalCapabilities(email_tools=[work_tool]))

        with patch("nova.plugins.mail.service.move_emails", new_callable=AsyncMock, return_value="moved") as mocked_move:
            moved = async_to_sync(executor.execute)(
                "mail move 10 11 --uid 12 --to-special junk --folder Inbox"
            )
        self.assertEqual(moved, "moved")
        mocked_move.assert_awaited_once_with(
            self.user,
            work_tool.id,
            message_ids=[10, 11],
            uids=[12],
            source_folder="Inbox",
            target_folder=None,
            target_special="junk",
        )

        with patch("nova.plugins.mail.service.mark_emails", new_callable=AsyncMock, return_value="marked") as mocked_mark:
            marked = async_to_sync(executor.execute)(
                "mail mark --uid 99 --uid 100 --flagged"
            )
        self.assertEqual(marked, "marked")
        mocked_mark.assert_awaited_once_with(
            self.user,
            work_tool.id,
            message_ids=[],
            uids=[99, 100],
            folder="INBOX",
            action="flagged",
        )

    def test_mail_move_requires_exactly_one_destination(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        executor = self._build_executor(TerminalCapabilities(email_tools=[work_tool]))

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mail move 10 --to-folder Archive --to-special junk")

    def test_calendar_accounts_and_calendars_use_account_registry(self):
        work_tool = self._create_caldav_tool(name="Work Calendar", username="work@example.com")
        personal_tool = self._create_caldav_tool(name="Personal Calendar", username="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool, personal_tool])
        )

        with patch(
            "nova.runtime.terminal.caldav_service.list_calendars",
            new_callable=AsyncMock,
            return_value=["Work", "Personal"],
        ) as mocked_list:
            accounts = async_to_sync(executor.execute)("calendar accounts")
            calendars = async_to_sync(executor.execute)("calendar calendars --account work@example.com")

        self.assertIn("work@example.com", accounts)
        self.assertIn("personal@example.com", accounts)
        self.assertIn("Available calendars:", calendars)
        self.assertIn("- Work", calendars)
        mocked_list.assert_awaited_once_with(self.user, work_tool.id)

    def test_calendar_command_requires_account_when_multiple_accounts_are_configured(self):
        work_tool = self._create_caldav_tool(name="Work Calendar", username="work@example.com")
        personal_tool = self._create_caldav_tool(name="Personal Calendar", username="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool, personal_tool])
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("calendar upcoming")

    def test_calendar_show_supports_json_output(self):
        work_tool = self._create_caldav_tool()
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool])
        )

        with patch(
            "nova.runtime.terminal.caldav_service.get_event_detail",
            new_callable=AsyncMock,
            return_value={
                "uid": "evt-1",
                "calendar_name": "Work",
                "summary": "Planning",
                "start": "2026-04-10T09:00:00+00:00",
                "end": "2026-04-10T10:00:00+00:00",
                "all_day": False,
                "location": "Room A",
                "description": "Roadmap review",
                "is_recurring": False,
            },
        ):
            result = async_to_sync(executor.execute)("calendar show evt-1 --output /calendar.json")

        content = async_to_sync(executor.execute)("cat /calendar.json")
        self.assertIn("Wrote calendar output to /calendar.json", result)
        self.assertIn('"uid": "evt-1"', content)

    def test_calendar_create_reads_description_file(self):
        work_tool = self._create_caldav_tool()
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool])
        )
        async_to_sync(executor.vfs.write_file)(
            "/details.md",
            b"Long meeting description",
            mime_type="text/markdown",
        )

        with patch(
            "nova.runtime.terminal.caldav_service.create_event",
            new_callable=AsyncMock,
            return_value={
                "uid": "evt-2",
                "calendar_name": "Work",
                "summary": "Planning",
                "start": "2026-04-10T09:00:00+00:00",
                "end": None,
                "all_day": False,
                "location": "",
                "description": "Long meeting description",
                "is_recurring": False,
            },
        ) as mocked_create:
            result = async_to_sync(executor.execute)(
                'calendar create --calendar Work --title "Planning" --start 2026-04-10T09:00:00+00:00 --description-file /details.md'
            )

        self.assertIn("Created event evt-2", result)
        self.assertEqual(mocked_create.await_args.kwargs["description"], "Long meeting description")

    def test_calendar_delete_requires_confirm(self):
        work_tool = self._create_caldav_tool()
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool])
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("calendar delete evt-1")

    def test_calendar_update_rejects_recurring_events_from_service(self):
        work_tool = self._create_caldav_tool()
        executor = self._build_executor(
            TerminalCapabilities(caldav_tools=[work_tool])
        )

        with patch(
            "nova.runtime.terminal.caldav_service.update_event",
            new_callable=AsyncMock,
            side_effect=ValueError("Recurring events are read-only in Nova."),
        ):
            with self.assertRaises(TerminalCommandError):
                async_to_sync(executor.execute)(
                    'calendar update evt-1 --calendar Work --title "Updated"'
                )
