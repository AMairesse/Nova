from __future__ import annotations

import logging
import time

from asgiref.sync import sync_to_async

from nova.message_utils import annotate_user_message
from nova.models.Message import Actor, Message
from nova.tasks.TaskExecutor import TaskExecutor
from nova.thread_titles import is_default_thread_subject
from nova.utils import markdown_to_html

from .agent import (
    ReactTerminalInterruptResult,
    ReactTerminalRunResult,
    ReactTerminalRuntime,
)
from .compaction import (
    approximate_token_count_from_text,
    build_v2_compaction_messages,
    format_messages_for_compaction,
    get_v2_compaction_error,
    get_v2_compaction_payload,
    store_v2_compaction_state,
)
from .provider_client import OpenAICompatibleProviderClient
from .sessions import get_or_create_agent_thread_session
from .support import get_v2_runtime_error

logger = logging.getLogger(__name__)


class ReactTerminalTaskExecutor(TaskExecutor):
    @staticmethod
    def _normalize_text_for_compare(value: str) -> str:
        return " ".join((value or "").split())

    def _get_display_markdown(self, final_answer: str) -> str:
        streamed_markdown = ""
        if self.handler and hasattr(self.handler, "get_streamed_markdown"):
            streamed_markdown = self.handler.get_streamed_markdown() or ""
        if not streamed_markdown:
            streamed_markdown = getattr(self.task, "streamed_markdown", "") or ""
        if not streamed_markdown.strip():
            return ""
        if self._normalize_text_for_compare(streamed_markdown) == self._normalize_text_for_compare(final_answer):
            return ""
        return streamed_markdown

    async def _create_llm_agent(self):
        runtime_error = get_v2_runtime_error(
            self.agent_config,
            thread_mode=getattr(self.thread, "mode", None),
        )
        if runtime_error:
            raise ValueError(runtime_error)

        await self.handler.record_progress("Creating React Terminal runtime")
        await self._ensure_trace_handler()
        self.runtime = await ReactTerminalRuntime(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent_config,
            task=self.task,
            trace_handler=self.trace_handler,
            progress_handler=self.handler,
            source_message_id=self.source_message_id,
        ).initialize()
        self.llm = None

    async def _run_agent(self):
        await self.handler.record_progress("Running React Terminal agent")
        return await self.runtime.run()

    async def _resume_agent(self, interruption_response):
        await self.handler.record_progress("Resuming React Terminal agent")
        return await self.runtime.run(
            resume_context=dict(interruption_response.get("resume_context") or {}),
            interruption_response=interruption_response,
        )

    def _extract_interruption_payload(self, result):
        if isinstance(result, ReactTerminalInterruptResult):
            return {
                "action": "ask_user",
                "question": result.question,
                "schema": dict(result.schema or {}),
                "agent_name": result.agent_name,
                "resume_context": dict(result.resume_context or {}),
            }
        return super()._extract_interruption_payload(result)

    async def _persist_agent_message_state(
        self,
        message_id: int,
        *,
        real_tokens: int | None,
        approx_tokens: int | None,
        max_context: int | None,
        display_markdown: str,
        trace_task_id: int | None,
        trace_summary: dict | None,
    ) -> None:
        def _persist_message_state() -> None:
            fresh_message = (
                Message.objects.select_related("thread", "user")
                .get(
                    id=message_id,
                    thread=self.thread,
                    user=self.user,
                )
            )
            annotate_user_message(fresh_message)
            internal_data = (
                fresh_message.internal_data
                if isinstance(fresh_message.internal_data, dict)
                else {}
            )
            internal_data.update({
                "real_tokens": real_tokens,
                "approx_tokens": approx_tokens,
                "max_context": max_context,
            })
            if trace_task_id is not None:
                internal_data["trace_task_id"] = trace_task_id
            if isinstance(trace_summary, dict):
                internal_data["trace_summary"] = trace_summary
            if display_markdown:
                internal_data["display_markdown"] = display_markdown
            else:
                internal_data.pop("display_markdown", None)
            fresh_message.internal_data = internal_data
            fresh_message.save(update_fields=["internal_data"])

        await sync_to_async(_persist_message_state, thread_sensitive=True)()

    async def _build_realtime_message_payload(self, message_id: int) -> dict:
        def _load_message():
            fresh_message = (
                Message.objects.select_related("thread", "user")
                .get(
                    id=message_id,
                    thread=self.thread,
                    user=self.user,
                )
            )
            annotate_user_message(fresh_message)
            display_text = ""
            if isinstance(fresh_message.internal_data, dict):
                display_text = str(fresh_message.internal_data.get("display_markdown") or "").strip()
            if not display_text:
                display_text = fresh_message.text or ""
            return {
                "id": fresh_message.id,
                "text": fresh_message.text,
                "actor": fresh_message.actor,
                "internal_data": fresh_message.internal_data,
                "created_at": str(fresh_message.created_at),
                "rendered_html": markdown_to_html(display_text),
                "artifacts": [],
            }

        return await sync_to_async(_load_message, thread_sensitive=True)()

    async def _process_result(self, result):
        if isinstance(result, ReactTerminalRunResult):
            run_result = result
        else:
            final_answer = "" if result is None else str(result)
            provider = await self._get_llm_provider()
            run_result = ReactTerminalRunResult(
                final_answer=final_answer,
                real_tokens=None,
                approx_tokens=None,
                max_context=getattr(provider, "max_context_tokens", None),
            )

        await self.handler.record_progress("Processing React Terminal result")
        final_answer = run_result.final_answer
        self.task.result = final_answer

        message = await sync_to_async(
            self.thread.add_message,
            thread_sensitive=False,
        )(final_answer, actor=Actor.AGENT)

        await self.handler.on_context_consumption(
            run_result.real_tokens,
            run_result.approx_tokens,
            run_result.max_context,
        )

        trace_summary = {
            "has_trace": False,
            "tool_calls": 0,
            "subagent_calls": 0,
            "interaction_count": 0,
            "error_count": 0,
            "artifact_count": 0,
            "duration_ms": None,
        }
        if self.trace_handler:
            await self.trace_handler.set_context_consumption(
                real_tokens=run_result.real_tokens,
                approx_tokens=run_result.approx_tokens,
                max_context=run_result.max_context,
            )
            await self.trace_handler.complete_root_run(final_answer)
            trace_summary = await self.trace_handler.get_message_trace_summary()

        display_markdown = self._get_display_markdown(final_answer)
        await self._persist_agent_message_state(
            message.id,
            real_tokens=run_result.real_tokens,
            approx_tokens=run_result.approx_tokens,
            max_context=run_result.max_context,
            display_markdown=display_markdown,
            trace_task_id=getattr(self.task, "id", None),
            trace_summary=trace_summary,
        )

        realtime_payload = await self._build_realtime_message_payload(message.id)
        await self.handler.on_new_message(
            realtime_payload,
            task_id=self.task.id,
        )

        self.task.current_response = None
        self.task.streamed_markdown = ""
        await self._enqueue_thread_title_generation()

    async def _enqueue_thread_title_generation(self):
        if not self.thread or not self.agent_config:
            return
        if not is_default_thread_subject(self.thread.subject):
            return

        enqueue_start = time.perf_counter()
        try:
            from nova.tasks.tasks import generate_thread_title_task

            await sync_to_async(generate_thread_title_task.delay, thread_sensitive=False)(
                thread_id=self.thread.id,
                user_id=self.user.id,
                agent_config_id=self.agent_config.id,
                source_task_id=self.task.id,
            )
            duration_ms = int((time.perf_counter() - enqueue_start) * 1000)
            logger.debug(
                "Enqueued v2 thread title generation (thread_id=%s, task_id=%s) in %sms.",
                getattr(self.thread, "id", None),
                getattr(self.task, "id", None),
                duration_ms,
            )
        except Exception as exc:
            logger.warning(
                "Could not enqueue v2 thread title generation (thread_id=%s, task_id=%s): %s",
                getattr(self.thread, "id", None),
                getattr(self.task, "id", None),
                exc,
            )

    async def _cleanup(self):
        return None


class ReactTerminalSummarizationTaskExecutor(TaskExecutor):
    def __init__(self, task, user, thread, agent_config):
        super().__init__(task, user, thread, agent_config, "", source_message_id=None)
        self.provider_client = None
        self.session = None

    async def execute(self):
        try:
            await self._initialize_task()
            await self._create_llm_agent()
            await self._perform_compaction()
            await self._finalize_task()
        except Exception as e:
            await self._handle_execution_error(e)
        finally:
            await self._cleanup()

    async def _create_llm_agent(self):
        runtime_error = get_v2_runtime_error(
            self.agent_config,
            thread_mode=getattr(self.thread, "mode", None),
        )
        if runtime_error:
            raise ValueError(runtime_error)
        compaction_error = get_v2_compaction_error(self.thread)
        if compaction_error:
            raise ValueError(compaction_error)
        await self.handler.record_progress("Preparing React Terminal compaction")
        self.provider_client = OpenAICompatibleProviderClient(await self._get_llm_provider())
        self.session = await get_or_create_agent_thread_session(self.thread, self.agent_config)
        self.llm = None

    async def _perform_compaction(self):
        payload = await get_v2_compaction_payload(self.thread, self.agent_config)
        state = payload["state"]
        messages_to_compact = list(payload["messages_to_compact"] or [])
        if not messages_to_compact:
            raise ValueError("Not enough messages to compact for React Terminal V1.")

        transcript = format_messages_for_compaction(messages_to_compact)
        await self.handler.record_progress("Generating compacted history summary")
        completion = await self.provider_client.create_chat_completion(
            messages=build_v2_compaction_messages(
                previous_summary=state["summary_markdown"],
                transcript=transcript,
            ),
            tools=None,
        )
        summary_markdown = str(completion.get("content") or "").strip()
        if not summary_markdown:
            raise ValueError("Compaction produced an empty summary.")

        await store_v2_compaction_state(
            self.session,
            summary_markdown=summary_markdown,
            summary_until_message_id=messages_to_compact[-1].id,
        )

        original_tokens = approximate_token_count_from_text(
            "\n\n".join(
                part for part in [state["summary_markdown"], transcript] if str(part or "").strip()
            )
        )
        summary_tokens = approximate_token_count_from_text(summary_markdown)
        self.task.result = "React Terminal history compaction completed."
        await self.handler.on_summarization_complete(
            summary_markdown,
            original_tokens,
            summary_tokens,
            "react_terminal_v1",
        )
        await self.handler.record_progress("React Terminal compaction completed", severity="success")

    async def _cleanup(self):
        return None
