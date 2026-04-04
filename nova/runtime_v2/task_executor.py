from __future__ import annotations

from asgiref.sync import sync_to_async

from nova.models.Message import Actor
from nova.tasks.TaskExecutor import TaskExecutor

from .agent import ReactTerminalRuntime
from .support import get_v2_runtime_error


class ReactTerminalTaskExecutor(TaskExecutor):
    async def _create_llm_agent(self):
        runtime_error = get_v2_runtime_error(
            self.agent_config,
            thread_mode=getattr(self.thread, "mode", None),
        )
        if runtime_error:
            raise ValueError(runtime_error)

        self.task.progress_logs.append(
            {
                "step": "Creating React Terminal runtime",
                "severity": "info",
            }
        )
        await sync_to_async(self.task.save, thread_sensitive=False)()
        await self._ensure_trace_handler()
        self.runtime = await ReactTerminalRuntime(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent_config,
            task=self.task,
            trace_handler=self.trace_handler,
            source_message_id=self.source_message_id,
        ).initialize()
        self.llm = None

    async def _run_agent(self):
        self.task.progress_logs.append(
            {
                "step": "Running React Terminal agent",
                "severity": "info",
            }
        )
        await sync_to_async(self.task.save, thread_sensitive=False)()
        return await self.runtime.run()

    async def _process_result(self, result):
        await super()._process_result(result)
        final_answer = "" if result is None else str(result)
        await sync_to_async(self.thread.add_message, thread_sensitive=False)(
            final_answer,
            actor=Actor.AGENT,
        )
        if self.trace_handler:
            await self.trace_handler.complete_root_run(final_answer)

    async def _cleanup(self):
        return None
