import time
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from nova.telemetry.langfuse import shutdown_langfuse_process_resources


class LangfuseTelemetryTests(SimpleTestCase):
    def test_shutdown_langfuse_process_resources_shuts_down_managers(self):
        manager = MagicMock()

        with patch(
            "nova.telemetry.langfuse._get_langfuse_resource_managers",
            return_value=[manager],
        ):
            shutdown_langfuse_process_resources(timeout_seconds=0.1)

        manager.shutdown.assert_called_once()

    def test_shutdown_langfuse_process_resources_times_out(self):
        manager = MagicMock()

        def slow_shutdown():
            time.sleep(0.05)

        manager.shutdown.side_effect = slow_shutdown

        with (
            patch(
                "nova.telemetry.langfuse._get_langfuse_resource_managers",
                return_value=[manager],
            ),
            self.assertLogs("nova.telemetry.langfuse", level="WARNING") as logs,
        ):
            shutdown_langfuse_process_resources(timeout_seconds=0.01)

        self.assertTrue(
            any("Timed out while shutting down Langfuse process resources" in line for line in logs.output),
            logs.output,
        )
