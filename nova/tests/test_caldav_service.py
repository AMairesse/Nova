from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from icalendar import Calendar as ICalendar
from icalendar import Event as ICalEvent

from nova.caldav import service as caldav_service
from nova.tests.factories import create_tool, create_tool_credential, create_user


class _FakeCalendarResource:
    def __init__(self, *components):
        calendar = ICalendar()
        calendar.add("prodid", "-//Tests//EN")
        calendar.add("version", "2.0")
        for component in components:
            calendar.add_component(component)
        self.icalendar_instance = calendar
        self.deleted = False
        self.saved = False

    def save(self):
        self.saved = True
        return self

    def delete(self):
        self.deleted = True


class _FakeCalendar:
    def __init__(self, name: str, *, search_results=None):
        self.name = name
        self._search_results = list(search_results or [])
        self.added_ical = None

    def search(self, **kwargs):
        del kwargs
        return list(self._search_results)

    def add_event(self, ical: str):
        self.added_ical = ical
        parsed = ICalendar.from_ical(ical)
        components = [component for component in parsed.walk() if component.name == "VEVENT"]
        return _FakeCalendarResource(*components)


class CaldavServiceTests(TestCase):
    def setUp(self):
        self.user = create_user(username="caldav-service", email="caldav-service@example.com")
        self.tool = create_tool(
            self.user,
            name="Work Calendar",
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
        )
        create_tool_credential(
            self.user,
            self.tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": "alice@example.com",
                "password": "secret",
            },
        )

    def _build_client(self, *calendars):
        return SimpleNamespace(principal=lambda: SimpleNamespace(calendars=lambda: list(calendars)))

    def test_list_events_normalizes_recurring_entries(self):
        component = ICalEvent()
        component.add("uid", "evt-1")
        component.add("summary", "Planning")
        component.add("dtstart", datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc))
        component.add("dtend", datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc))
        component.add("rrule", {"freq": ["weekly"]})
        resource = _FakeCalendarResource(component)
        calendar = _FakeCalendar("Work", search_results=[resource])

        with patch(
            "nova.caldav.service._get_caldav_client_sync",
            return_value=self._build_client(calendar),
        ):
            events = asyncio.run(
                caldav_service.list_events(
                    self.user,
                    self.tool.id,
                    start_date="2026-04-01T00:00:00+00:00",
                    end_date="2026-04-30T23:59:59+00:00",
                    calendar_name="Work",
                )
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["uid"], "evt-1")
        self.assertEqual(events[0]["calendar_name"], "Work")
        self.assertTrue(events[0]["is_recurring"])

    def test_create_event_returns_normalized_payload(self):
        calendar = _FakeCalendar("Work")

        with patch(
            "nova.caldav.service._get_caldav_client_sync",
            return_value=self._build_client(calendar),
        ):
            event = asyncio.run(
                caldav_service.create_event(
                    self.user,
                    self.tool.id,
                    calendar_name="Work",
                    summary="Planning",
                    start="2026-04-10T09:00:00+00:00",
                    end="2026-04-10T10:00:00+00:00",
                    location="Room A",
                    description="Roadmap review",
                )
            )

        self.assertEqual(event["summary"], "Planning")
        self.assertEqual(event["calendar_name"], "Work")
        self.assertEqual(event["location"], "Room A")
        self.assertIn("Roadmap review", calendar.added_ical)

    def test_update_event_rejects_recurring_event(self):
        component = ICalEvent()
        component.add("uid", "evt-2")
        component.add("summary", "Recurring")
        component.add("dtstart", datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc))
        component.add("rrule", {"freq": ["weekly"]})
        resource = _FakeCalendarResource(component)
        calendar = _FakeCalendar("Work", search_results=[resource])

        with patch(
            "nova.caldav.service._get_caldav_client_sync",
            return_value=self._build_client(calendar),
        ):
            with self.assertRaisesMessage(ValueError, "Recurring events are read-only"):
                asyncio.run(
                    caldav_service.update_event(
                        self.user,
                        self.tool.id,
                        event_id="evt-2",
                        calendar_name="Work",
                        summary="Updated",
                    )
                )
