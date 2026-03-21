from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
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
            ("done", {"artifact_ids": [9], "tool_output": True}),
            run_id=UUID(int=3),
        )
        await handler.complete_subagent(
            subagent_node_id,
            output_preview="Finished delegated research",
            artifact_refs=[{"artifact_id": 9, "tool_output": True}],
        )
        await handler.complete_root_run("Done")

        trace = task.execution_trace
        self.assertEqual(trace["summary"]["tool_calls"], 2)
        self.assertEqual(trace["summary"]["subagent_calls"], 1)
        self.assertEqual(trace["summary"]["artifact_count"], 1)
        self.assertTrue(trace["summary"]["has_trace"])
        self.assertEqual(trace["root"]["status"], "completed")
        self.assertEqual(trace["root"]["meta"]["source_message_id"], 55)

        root_children = trace["root"]["children"]
        self.assertEqual(root_children[0]["type"], "tool")
        self.assertEqual(root_children[1]["type"], "subagent")
        self.assertEqual(root_children[1]["children"][0]["type"], "tool")
        self.assertEqual(root_children[1]["children"][0]["artifact_refs"][0]["artifact_id"], 9)

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
