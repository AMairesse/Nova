from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch
from uuid import UUID

from nova.tasks.execution_trace import TaskExecutionTraceHandler


class TaskExecutionTraceHandlerTests(IsolatedAsyncioTestCase):
    async def test_records_tools_and_nested_subagents_without_agent_tool_duplicates(self):
        task = SimpleNamespace(id=901, execution_trace={})
        handler = TaskExecutionTraceHandler(
            task,
            ignored_tool_names={"agent_delegate"},
        )

        await handler.ensure_root_run(label="Planner", source_message_id=55, agent_id=7)
        await handler.on_tool_start({"name": "web_search"}, '{"query": "nova"}', run_id=UUID(int=1))
        await handler.on_tool_end({"result": "ok"}, run_id=UUID(int=1))

        await handler.on_tool_start({"name": "agent_delegate"}, '{"question": "ignored"}', run_id=UUID(int=2))
        await handler.on_tool_end("ignored", run_id=UUID(int=2))

        subagent_node_id = await handler.start_subagent(label="Delegate", input_preview="Research Nova")
        child_handler = handler.clone_for_parent(parent_node_id=subagent_node_id)
        await child_handler.on_tool_start({"name": "web_fetch"}, '{"url": "https://example.com"}', run_id=UUID(int=3))
        await child_handler.on_tool_end(
            {"result": "done"},
            run_id=UUID(int=3),
        )
        await handler.complete_subagent(
            subagent_node_id,
            output_preview="Finished delegated research",
        )
        await handler.complete_root_run("Done")

        trace = task.execution_trace
        self.assertEqual(trace["summary"]["tool_calls"], 2)
        self.assertEqual(trace["summary"]["subagent_calls"], 1)
        self.assertTrue(trace["summary"]["has_trace"])
        self.assertEqual(trace["root"]["status"], "completed")
        self.assertEqual(trace["root"]["meta"]["source_message_id"], 55)

        root_children = trace["root"]["children"]
        self.assertEqual(root_children[0]["type"], "tool")
        self.assertEqual(root_children[1]["type"], "subagent")
        self.assertEqual(root_children[1]["children"][0]["type"], "tool")

    async def test_records_interactions_and_errors(self):
        task = SimpleNamespace(id=902, execution_trace={})
        handler = TaskExecutionTraceHandler(task)

        await handler.ensure_root_run(label="Planner")
        await handler.record_interaction(
            question="Continue?",
            schema={"type": "object"},
            agent_name="Planner",
        )
        await handler.mark_root_awaiting_input()

        trace = task.execution_trace
        self.assertEqual(trace["summary"]["interaction_count"], 1)
        self.assertEqual(trace["root"]["status"], "awaiting_input")

        await handler.fail_root_run("boom", category="tool_failure")

        trace = task.execution_trace
        self.assertEqual(trace["summary"]["error_count"], 1)
        self.assertEqual(trace["root"]["status"], "failed")
        self.assertEqual(trace["root"]["meta"]["category"], "tool_failure")


class TaskExecutionTraceHandlerCrossLoopTests(TestCase):
    @patch("nova.models.Task.Task.objects.filter")
    def test_tool_callbacks_remain_safe_across_event_loops(self, mock_filter):
        update_started = threading.Event()
        release_update = threading.Event()

        mock_qs = MagicMock()

        def slow_update(**kwargs):
            update_started.set()
            release_update.wait(timeout=2.0)
            return 1

        mock_qs.update.side_effect = slow_update
        mock_filter.return_value = mock_qs

        task = SimpleNamespace(id=903, execution_trace={})
        handler = TaskExecutionTraceHandler(task)
        errors: list[BaseException] = []

        def first_loop():
            try:
                asyncio.run(handler.ensure_root_run(label="Planner"))
            except BaseException as exc:  # pragma: no cover - only for assertion capture
                errors.append(exc)

        first_thread = threading.Thread(target=first_loop)
        first_thread.start()

        self.assertTrue(update_started.wait(timeout=1.0))
        try:
            asyncio.run(
                handler.on_tool_start(
                    {"name": "web_search"},
                    '{"query": "nova"}',
                    run_id=UUID(int=1),
                )
            )
        except BaseException as exc:  # pragma: no cover - only for assertion capture
            errors.append(exc)
        finally:
            release_update.set()
            first_thread.join(timeout=2.0)

        self.assertEqual(errors, [])
        self.assertEqual(task.execution_trace["summary"]["tool_calls"], 1)
