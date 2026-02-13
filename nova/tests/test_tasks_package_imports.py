from __future__ import annotations

import importlib
import sys

from django.test import SimpleTestCase


class TasksPackageImportTests(SimpleTestCase):
    def test_tasks_package_imports_transcript_index_tasks_module(self):
        # Reload package from scratch to ensure registration comes from
        # nova.tasks.__init__ imports.
        sys.modules.pop("nova.tasks.transcript_index_tasks", None)
        sys.modules.pop("nova.tasks", None)

        tasks_pkg = importlib.import_module("nova.tasks")

        self.assertTrue(hasattr(tasks_pkg, "transcript_index_tasks"))
        self.assertIn("nova.tasks.transcript_index_tasks", sys.modules)

