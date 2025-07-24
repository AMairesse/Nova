# nova/tests/test_caldav_describe_events.py
from __future__ import annotations

from datetime import datetime, timezone as dt_tz
from unittest.mock import Mock

from django.test import SimpleTestCase

from nova.tools.builtins.caldav import describe_events


class DescribeEventsTests(SimpleTestCase):
    def _make_vevent(self, summary="Title", desc="Desc") -> Mock:
        """Return a dummy icalendar VEVENT component."""
        comp = Mock()
        comp.name = "VEVENT"
        comp.get.side_effect = lambda field: {
            "summary": summary,
            "description": desc,
            "dtstart": Mock(dt=datetime(2025, 1, 1, 10, 0, tzinfo=dt_tz.utc)),
            "dtend": Mock(dt=datetime(2025, 1, 1, 11, 0, tzinfo=dt_tz.utc)),
            "location": None,
            "UID": "uid-123",
        }.get(field)
        return comp

    def test_basic_render(self):
        evt = Mock()
        evt.icalendar_instance.walk.return_value = [self._make_vevent()]
        res = describe_events([evt])

        self.assertEqual(len(res), 1)
        txt = res[0]
        self.assertIn("Event name :", txt)
        self.assertIn("Start :", txt)
        self.assertIn("End :", txt)

    def test_skips_non_vevent(self):
        non_evt = Mock()
        non_evt.icalendar_instance.walk.return_value = []
        res = describe_events([non_evt])

        self.assertEqual(res, [])
