# nova/tests/test_forms.py
"""
Current regression-tests for Nova forms.

Out-of-date CalendarSelectionForm checks were removed – that form no longer
exists.  We now cover:

• UserParametersForm – simple Boolean flag round-trip.
• ToolForm
    – Built-in tool auto-fill (CalDav).
    – Validation errors for incomplete API/MCP tools.
"""

from __future__ import annotations

import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.encoding import force_str

from nova.forms import UserParametersForm, ToolForm
from nova.models import UserParameters, Tool

User = get_user_model()


class UserParametersFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("alice", password="pwd")
        self.params = UserParameters.objects.create(user=self.user)

    def test_toggle_allow_langfuse(self):
        """Boolean flag is saved correctly."""
        form = UserParametersForm(
            {"allow_langfuse": "on"}, instance=self.params
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertTrue(inst.allow_langfuse)

        # Un-tick
        form = UserParametersForm(
            {"allow_langfuse": ""}, instance=self.params
        )
        self.assertTrue(form.is_valid(), form.errors)
        inst = form.save()
        self.assertFalse(inst.allow_langfuse)


class ToolFormTests(TestCase):
    #
    # Built-in (“caldav”) helpers
    # ------------------------------------------------------------------ #
    def _builtin_post_data(self, subtype: str = "caldav") -> dict[str, str]:
        return {
            "tool_type": Tool.ToolType.BUILTIN,
            "tool_subtype": subtype,
            # Name / description intentionally left blank – must be auto-filled
            "is_active": "on",
        }

    #
    # 1) Built-in tool auto-fill
    # ------------------------------------------------------------------ #
    def test_builtin_autofill(self):
        """For builtin tools, name / description / python_path are injected."""
        data = self._builtin_post_data()
        form = ToolForm(data)
        self.assertTrue(form.is_valid(), form.errors)

        inst: Tool = form.save(commit=False)  # do **not** hit the DB
        self.assertEqual(force_str(inst.name), "CalDav")
        self.assertIn("CalDav", force_str(inst.description))
        self.assertEqual(inst.python_path, "nova.tools.builtins.caldav")
        self.assertEqual(inst.tool_type, Tool.ToolType.BUILTIN)
        self.assertTrue(inst.is_active)

    #
    # 2) Missing required fields for API / MCP tools
    # ------------------------------------------------------------------ #
    def test_api_requires_name_description_endpoint(self):
        """API tools without required fields must raise validation errors."""
        data = {
            "tool_type": Tool.ToolType.API,
            "is_active": "on",
            # name / description / endpoint omitted on purpose
        }
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("description", form.errors)
        self.assertIn("endpoint", form.errors)

    def test_mcp_requires_endpoint(self):
        """MCP tools must include at least a name, description and endpoint."""
        data = {
            "tool_type": Tool.ToolType.MCP,
            "name": "My remote tools",
            "description": "Container of remote tools",
            "is_active": "on",
            # endpoint missing
        }
        form = ToolForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("endpoint", form.errors)
