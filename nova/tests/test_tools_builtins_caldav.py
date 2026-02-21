from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.models.Tool import Tool
from nova.tests.factories import create_tool, create_tool_credential, create_user
from nova.tools.builtins import caldav as caldav_tools


class CaldavBuiltinsTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(username="caldav-user", email="caldav@example.com")
        self.tool = create_tool(
            self.user,
            name="CalDav tool",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": "alice",
                "password": "secret",
            },
        )

    @patch("nova.tools.builtins.caldav.ToolCredential.objects.get")
    def test_get_caldav_client_requires_complete_config(self, mocked_get_credential):
        mocked_get_credential.return_value = SimpleNamespace(
            config={"caldav_url": "https://cal.example.com", "username": "alice"}
        )

        with self.assertRaisesMessage(ValueError, "Incomplete CalDav configuration"):
            asyncio.run(caldav_tools.get_caldav_client(self.user, self.tool.id))

    @patch("nova.tools.builtins.caldav.get_caldav_client", new_callable=AsyncMock)
    def test_list_events_returns_calendar_not_found(self, mocked_client):
        principal = SimpleNamespace(calendars=lambda: [SimpleNamespace(name="Personal")])
        mocked_client.return_value = SimpleNamespace(principal=lambda: principal)

        result = asyncio.run(
            caldav_tools.list_events(
                self.user,
                self.tool.id,
                start_date="2026-02-01",
                end_date="2026-02-10",
                calendar_name="Work",
            )
        )

        self.assertIn("Calendar 'Work' not found.", result)

    def test_describe_events_formats_vevent(self):
        component = SimpleNamespace(
            name="VEVENT",
            get=lambda key: {
                "summary": "Planning",
                "description": "Roadmap",
                "dtstart": SimpleNamespace(dt=datetime(2026, 2, 10, 9, 0, tzinfo=timezone.utc)),
                "dtend": SimpleNamespace(dt=datetime(2026, 2, 10, 10, 0, tzinfo=timezone.utc)),
                "location": "Room A",
                "UID": "evt-1",
            }.get(key),
        )
        event = SimpleNamespace(icalendar_instance=SimpleNamespace(walk=lambda: [component]))

        lines = caldav_tools.describe_events([event])

        self.assertEqual(len(lines), 1)
        self.assertIn("Event name :Planning", lines[0])
        self.assertIn("UID : evt-1", lines[0])

    @patch("nova.tools.builtins.caldav.list_calendars", new_callable=AsyncMock, return_value="Available calendars :\n- A\n- B\n")
    def test_test_caldav_access_reports_pluralized_count(self, mocked_list_calendars):
        result = asyncio.run(caldav_tools.test_caldav_access(self.user, self.tool.id))

        self.assertEqual(result["status"], "success")
        self.assertIn("2 calendars found", result["message"])
        mocked_list_calendars.assert_awaited_once_with(self.user, self.tool.id)

    def test_get_functions_validates_required_tool_data(self):
        invalid_tool = Tool(
            user=self.user,
            name="invalid",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        with self.assertRaisesMessage(ValueError, "missing required data"):
            asyncio.run(caldav_tools.get_functions(invalid_tool, agent=None))

    def test_get_functions_returns_expected_tool_names(self):
        tools = asyncio.run(caldav_tools.get_functions(self.tool, agent=None))
        names = [tool.name for tool in tools]

        self.assertIn("list_calendars", names)
        self.assertIn("list_events_to_come", names)
        self.assertIn("get_event_detail", names)
        self.assertIn("search_events", names)

    def test_metadata_marks_caldav_as_skill(self):
        loading = (caldav_tools.METADATA or {}).get("loading", {})

        self.assertEqual(loading.get("mode"), "skill")
        self.assertEqual(loading.get("skill_id"), "caldav")
        self.assertEqual(loading.get("skill_label"), "CalDav")

    def test_get_skill_instructions_returns_non_empty_list(self):
        instructions = caldav_tools.get_skill_instructions()

        self.assertIsInstance(instructions, list)
        self.assertTrue(any(str(item).strip() for item in instructions))

    @patch("nova.tools.builtins.caldav.list_calendars", new_callable=AsyncMock, return_value="ok")
    def test_get_aggregated_functions_routes_calls_by_calendar_account(self, mocked_list_calendars):
        second_tool = create_tool(
            self.user,
            name="Second CalDav",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "caldav_url": "https://cal2.example.com",
                "username": "bob@example.com",
                "password": "secret",
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        tools = asyncio.run(caldav_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))
        list_tool = next(tool for tool in tools if tool.name == "list_calendars")

        first = asyncio.run(list_tool.coroutine(calendar_account="alice"))
        second = asyncio.run(list_tool.coroutine(calendar_account="bob@example.com"))

        self.assertEqual(first, "ok")
        self.assertEqual(second, "ok")
        self.assertEqual(mocked_list_calendars.await_count, 2)
        self.assertEqual(mocked_list_calendars.await_args_list[0].args[1], self.tool.id)
        self.assertEqual(mocked_list_calendars.await_args_list[1].args[1], second_tool.id)

    def test_get_aggregated_functions_rejects_unknown_calendar_account(self):
        second_tool = create_tool(
            self.user,
            name="Second CalDav",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "caldav_url": "https://cal2.example.com",
                "username": "bob@example.com",
                "password": "secret",
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        tools = asyncio.run(caldav_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))
        list_tool = next(tool for tool in tools if tool.name == "list_calendars")

        result = asyncio.run(list_tool.coroutine(calendar_account="unknown@example.com"))
        self.assertIn("Unknown calendar_account", result)
        self.assertIn("alice", result)
        self.assertIn("bob@example.com", result)

    @patch("nova.tools.builtins.caldav.list_calendars", new_callable=AsyncMock)
    def test_get_aggregated_functions_rejects_ambiguous_calendar_account(self, mocked_list_calendars):
        second_tool = create_tool(
            self.user,
            name="Shared CalDav",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "caldav_url": "https://cal2.example.com",
                "username": "alice",
                "password": "secret",
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        tools = asyncio.run(caldav_tools.get_aggregated_functions([self.tool, second_tool], agent=agent))
        list_tool = next(tool for tool in tools if tool.name == "list_calendars")

        result = asyncio.run(list_tool.coroutine(calendar_account="alice"))
        self.assertIn("Ambiguous calendar_account", result)
        mocked_list_calendars.assert_not_awaited()

    def test_get_aggregated_prompt_instructions_include_calendar_account_map(self):
        second_tool = create_tool(
            self.user,
            name="Team Calendar",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            second_tool,
            config={
                "caldav_url": "https://cal2.example.com",
                "username": "team@example.com",
                "password": "secret",
            },
        )

        agent = SimpleNamespace(user=self.user, builtin_tools=[self.tool, second_tool])
        hints = asyncio.run(
            caldav_tools.get_aggregated_prompt_instructions([self.tool, second_tool], agent=agent)
        )
        joined = "\n".join(hints)

        self.assertIn("CalDAV account map:", joined)
        self.assertIn("alice", joined)
        self.assertIn("team@example.com", joined)
        self.assertIn("calendar_account", joined)

    def test_aggregation_spec_requires_multi_instance(self):
        self.assertEqual(caldav_tools.AGGREGATION_SPEC.get("min_instances"), 2)
