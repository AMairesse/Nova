# nova/tests/test_caldav.py
"""
Unit-tests for nova.tools.builtins.caldav    (no network access required)

The CalDav client itself is stubbed with `unittest.mock.patch`.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_tz, timedelta
from typing import List
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from nova.models import Tool, ToolCredential
from nova.tools.builtins import caldav as cd


# --------------------------------------------------------------------------- #
#  Test helpers                                                               #
# --------------------------------------------------------------------------- #
def _create_user_and_tool() -> tuple[User, Tool]:
    """
    Minimal (User, Tool, Credential) trio required by the caldav helper.
    """
    user = User.objects.create_user("alice", password="pwd")
    tool = Tool.objects.create(
        user=user,
        name="CalDav",
        description="CalDav test tool",
        tool_type=Tool.ToolType.BUILTIN,
        python_path="nova.tools.builtins.caldav",
        is_active=True,
        tool_subtype="caldav",
    )

    # The implementation expects ALL fields in the JSON config
    ToolCredential.objects.create(
        user=user,
        tool=tool,
        auth_type="basic",
        # username / password are stored inside `config`, not in the columns
        config={
            "caldav_url": "http://test.com",
            "username": "bob",
            "password": "secret",
        },
    )
    return user, tool


# --------------------------------------------------------------------------- #
#  Test case                                                                   #
# --------------------------------------------------------------------------- #
class CalDavToolTests(TestCase):
    """Exercise the Nova glue-code – we DO NOT test the real caldav client."""

    def setUp(self) -> None:
        self.user, self.tool = _create_user_and_tool()

    # ------------------------------------------------------------------ #
    #  get_caldav_client                                                 #
    # ------------------------------------------------------------------ #
    @patch("nova.tools.builtins.caldav.caldav.DAVClient")
    def test_get_caldav_client_success(self, mock_client_cls):
        """A valid credential returns a configured `caldav.DAVClient`."""
        client_instance = Mock()
        mock_client_cls.return_value = client_instance

        client = cd.get_caldav_client(self.user, self.tool.id)

        self.assertIs(client, client_instance)
        mock_client_cls.assert_called_once_with(
            url="http://test.com", username="bob", password="secret"
        )

    def test_get_caldav_client_missing_credential(self):
        """Absence of a credential raises a human-readable ValueError."""
        ToolCredential.objects.all().delete()

        with self.assertRaises(ValueError) as ctx:
            cd.get_caldav_client(self.user, self.tool.id)

        # Message should mention “No CalDav credential …”
        self.assertIn("No CalDav credential found for tool", str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  list_calendars                                                    #
    # ------------------------------------------------------------------ #
    @patch("nova.tools.builtins.caldav.caldav.DAVClient")
    def test_list_calendars_formats_output(self, mock_client_cls):
        """The helper returns a bullet list of calendar names."""
        mock_cal1 = Mock(name="One")
        mock_cal1.name = "One"
        mock_cal2 = Mock(name="Two")
        mock_cal2.name = "Two"

        principal = Mock()
        principal.calendars.return_value = [mock_cal1, mock_cal2]
        mock_client_cls.return_value.principal.return_value = principal

        out = cd.list_calendars(self.user, self.tool.id)

        self.assertIn("Available calendars", out)
        self.assertIn("- One", out)
        self.assertIn("- Two", out)

    # ------------------------------------------------------------------ #
    #  list_events_to_come                                               #
    # ------------------------------------------------------------------ #
    @patch("nova.tools.builtins.caldav.list_events")
    def test_list_events_to_come_delegates(self, mock_list_events):
        """We simply verify that list_events_to_come forwards arguments."""
        mock_list_events.return_value = "dummy"

        result = cd.list_events_to_come(self.user, self.tool.id, days_ahead=3)

        mock_list_events.assert_called_once()
        self.assertEqual(result, "dummy")

    # ------------------------------------------------------------------ #
    #  list_events – empty calendar list                                 #
    # ------------------------------------------------------------------ #
    @patch("nova.tools.builtins.caldav.caldav.DAVClient")
    def test_list_events_no_calendar(self, mock_client_cls):
        """
        When the CalDav principal has no calendars, the helper returns the
        translatable “No calendars available.” message.
        """
        principal = Mock()
        principal.calendars.return_value = []
        mock_client_cls.return_value.principal.return_value = principal

        out = cd.list_events(
            self.user,
            self.tool.id,
            start_date="2025-01-01",
            end_date="2025-01-02",
            calendar_name=None,
        )
        self.assertIn("No calendars available", out)
