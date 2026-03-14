import sys
import time
import types
from uuid import uuid4
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from nova.telemetry.langfuse import (
    _safe_propagate_attributes,
    create_langfuse_callback_handler,
    shutdown_langfuse_process_resources,
)


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

    def test_safe_propagate_attributes_ignores_detach_context_errors(self):
        context_manager = _safe_propagate_attributes(session_id="thread-123")
        context_manager.__enter__()

        with patch(
            "opentelemetry.context._RUNTIME_CONTEXT.detach",
            side_effect=ValueError("token created in a different Context"),
        ) as mocked_detach:
            context_manager.__exit__(None, None, None)

        mocked_detach.assert_called_once()

    def test_create_langfuse_callback_handler_patches_propagation_and_cleans_up_on_chain_error(self):
        langfuse_module = types.ModuleType("langfuse")
        langfuse_module.__path__ = []
        langfuse_langchain = types.ModuleType("langfuse.langchain")
        callback_module = types.ModuleType("langfuse.langchain.CallbackHandler")

        class FakeCallbackHandler:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.error_calls = []
                self.reset_calls = 0

            def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
                self.end_call = {
                    "outputs": outputs,
                    "run_id": run_id,
                    "parent_run_id": parent_run_id,
                    "kwargs": kwargs,
                }

            def on_chain_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs):
                self.error_calls.append(
                    {
                        "error": error,
                        "run_id": run_id,
                        "parent_run_id": parent_run_id,
                        "tags": tags,
                        "kwargs": kwargs,
                    }
                )

            def _reset(self):
                self.reset_calls += 1

        langfuse_langchain.CallbackHandler = FakeCallbackHandler
        callback_module.propagate_attributes = object()

        with patch.dict(
            sys.modules,
            {
                "langfuse": langfuse_module,
                "langfuse.langchain": langfuse_langchain,
                "langfuse.langchain.CallbackHandler": callback_module,
            },
        ):
            handler = create_langfuse_callback_handler(public_key="pk-test")

        self.assertEqual(handler.kwargs, {"public_key": "pk-test"})
        self.assertIs(callback_module.propagate_attributes, _safe_propagate_attributes)

        propagation_context_manager = MagicMock()
        handler._propagation_context_manager = propagation_context_manager

        handler.on_chain_error(RuntimeError("boom"), run_id=uuid4(), parent_run_id=None)

        propagation_context_manager.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(handler.reset_calls, 1)
        self.assertIsNone(handler._propagation_context_manager)
