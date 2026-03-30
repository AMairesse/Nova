# nova/tasks/TaskProgressHandler.py
import logging
from uuid import UUID
from time import monotonic
from typing import Any, Dict, List, Optional
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from langchain_core.callbacks import AsyncCallbackHandler

from nova.utils import markdown_to_html

logger = logging.getLogger(__name__)


# Custom callback handler for synthesis and streaming
class TaskProgressHandler(AsyncCallbackHandler):
    def __init__(
        self,
        task_id,
        channel_layer,
        *,
        user_id: int | None = None,
        thread_id: int | None = None,
        thread_mode: str | None = None,
        initial_streamed_markdown: str | None = None,
        push_notifications_enabled: bool = True,
    ):
        self.task_id = task_id
        self.channel_layer = channel_layer
        self.user_id = user_id
        self.thread_id = thread_id
        self.thread_mode = thread_mode
        self.push_notifications_enabled = push_notifications_enabled
        initial_stream = (initial_streamed_markdown or "")
        self.final_chunks = [initial_stream] if initial_stream else []
        self.current_tool = None
        self.tool_depth = 0
        self.token_count = 0
        self._last_persist_count = 0
        self._persist_interval = 50  # Persist every 50 tokens
        self._stream_flush_interval_seconds = 0.25
        self._last_stream_flush_at = monotonic()
        self._last_stream_html = None
        self._stream_has_pending_changes = False
        self._runtime_touch_interval_seconds = 15.0
        self._last_runtime_touch_at = None
        # Insert a markdown paragraph break when a new agent segment starts
        # after an explicit boundary (tool call, interruption/resume).
        self._needs_segment_break = False

    async def publish_update(self, message_type, data):
        await self.channel_layer.group_send(
            f'task_{self.task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    async def on_interrupt(self, interaction_id, question, schema, agent_name):
        '''
        Send a message to the client when the task is interrupted
        '''
        flushed_html = await self._flush_stream_chunk()
        await self._persist_stream_state(flushed_html)
        if self.final_chunks:
            self._needs_segment_break = True
        await self.publish_update('user_prompt', {'interaction_id': interaction_id, 'question': question,
                                                  'schema': schema, 'origin_name': agent_name})

    async def on_resume_task(self, interruption_response):
        '''
        Send a message to the client when the task is resumed
        '''
        await self.publish_update('interaction_update',
                                  {'interaction_id': interruption_response['interaction_id'],
                                   'interaction_status': interruption_response['interaction_status']})

    async def on_task_complete(self, result, thread_id, thread_subject):
        '''
        Send a message to the client when the task is completed
        '''
        await self.publish_update('task_complete', {'result': result, 'thread_id': thread_id,
                                                    'thread_subject': thread_subject})
        self._queue_push_notification(status="completed")

    async def on_error(self, error_msg, error_category):
        '''
        Send a message to the client when an error occurs
        '''
        flushed_html = None
        if self.tool_depth == 0:
            flushed_html = await self._flush_stream_chunk()
        await self._persist_stream_state(flushed_html)
        await self.publish_update('task_error', {'message': error_msg, 'category': error_category})
        self._queue_push_notification(status="failed")

    async def on_progress(self, message):
        '''
        Send a message to the client when the task is in progress
        '''
        await self._touch_task_runtime()
        await self.publish_update('progress_update', {'progress_log': message})

    async def on_chunk(self, chunk):
        '''
        Send a message to the client when a chunk is generated
        '''
        await self.publish_update('response_chunk', {'chunk': chunk})

    async def on_context_consumption(self, real, approx, max):
        '''
        Send a message to the client with context consumption info
        '''
        await self.publish_update('context_consumption', {'real_tokens': real, 'approx_tokens': approx,
                                                          'max_context': max})

    async def on_new_message(self, message_payload, *, task_id=None):
        payload = {'message': message_payload}
        if task_id is not None:
            payload['task_id'] = task_id
        await self.publish_update('new_message', payload)

    async def on_summarization_complete(self, summary_text, original_tokens, summary_tokens, strategy):
        '''
        Send a message to the client when summarization is completed
        '''
        await self.publish_update('summarization_complete', {
            'summary': summary_text,
            'original_tokens': original_tokens,
            'summary_tokens': summary_tokens,
            'strategy': strategy,
            'timestamp': None  # Will be set by client
        })

    async def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], *, run_id: UUID,
                             parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None,
                             metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        '''
        Mandatory method for langchain
        '''
        pass

    async def on_chain_end(self, outputs: Dict[str, Any], *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                           tags: Optional[List[str]] = None, **kwargs: Any) -> None:
        '''
        Mandatory method for langchain
        '''
        pass

    async def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any):
        '''
        Mandatory method for langchain
        '''
        try:
            if self.tool_depth == 0:
                await self.on_progress("Agent started")
            else:
                await self.on_progress("Sub-agent started")
        except Exception as e:
            logger.error(f"Error in on_llm_start: {e}")

    async def on_llm_new_token(self, token: str, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                               **kwargs: Any) -> Any:
        '''
        Mandatory method for langchain
        '''
        try:
            # Send only chunks from the root run
            if self.tool_depth == 0:
                if self._needs_segment_break:
                    self._append_segment_break_before_token(token)
                    self._needs_segment_break = False
                self.final_chunks.append(token)
                self._stream_has_pending_changes = True
                current_html = None
                now = monotonic()
                sentence_boundary = token.endswith((".", "!", "?", ";", ":"))
                line_boundary = "\n" in token
                should_flush = (
                    line_boundary
                    or sentence_boundary
                    or (now - self._last_stream_flush_at) >= self._stream_flush_interval_seconds
                )

                if should_flush:
                    current_html = await self._flush_stream_chunk()

                # Persist periodically for recovery
                self.token_count += 1
                if self.token_count - self._last_persist_count >= self._persist_interval:
                    if current_html is None:
                        current_html = self._render_current_response()
                    await self._persist_stream_state(current_html)
                    self._last_persist_count = self.token_count
            else:
                # If a sub agent is generating a response,
                # send it as a progress update every 100 tokens
                self.token_count += 1
                if self.token_count % 100 == 0:
                    await self.on_progress("Sub-agent still working...")
        except Exception as e:
            logger.error(f"Error in on_llm_new_token: {e}")

    async def on_llm_end(self, response: Any, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                         **kwargs: Any) -> Any:
        '''
        Mandatory method for langchain
        '''
        try:
            if self.tool_depth == 0:
                current_html = await self._flush_stream_chunk()
                await self._persist_stream_state(current_html)
                await self.on_progress("Agent finished")
            else:
                await self.on_progress("Sub-agent finished")
        except Exception as e:
            logger.error(f"Error in on_llm_end: {e}")

    def _render_current_response(self):
        return markdown_to_html(''.join(self.final_chunks))

    async def _flush_stream_chunk(self):
        if not self.final_chunks or not self._stream_has_pending_changes:
            return self._last_stream_html

        clean_html = self._render_current_response()
        if clean_html != self._last_stream_html:
            await self.on_chunk(clean_html)
            self._last_stream_html = clean_html

        self._last_stream_flush_at = monotonic()
        self._stream_has_pending_changes = False
        return clean_html

    def get_streamed_markdown(self) -> str:
        return ''.join(self.final_chunks)

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, *, run_id: UUID,
                            parent_run_id: Optional[UUID] = None, tags: Optional[List[str]] = None,
                            metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        '''
        Mandatory method for langchain
        '''
        try:
            # If a tool is starting,
            # store it to avoid sending response chunks back to the user
            tool_name = serialized.get('name', 'Unknown')
            if self.tool_depth == 0 and self.final_chunks:
                self._needs_segment_break = True
            self.current_tool = tool_name
            self.tool_depth += 1
            await self.on_progress(f"Tool '{tool_name}' started")
        except Exception as e:
            logger.error(f"Error in on_tool_start: {e}")

    async def on_tool_end(self, output: Any, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                          **kwargs: Any) -> Any:
        '''
        Mandatory method for langchain
        '''
        try:
            await self.on_progress(f"Tool '{self.current_tool}' finished")
            # If a tool is ending, reset the current tool so that we may
            # send response chunks if the main agent is generating
            self.current_tool = None
            self.tool_depth -= 1
        except Exception as e:
            logger.error(f"Error in on_tool_end: {e}")

    async def on_agent_finish(self, finish: Any, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                              **kwargs: Any) -> Any:
        '''
        Mandatory method for langchain
        '''
        try:
            if self.tool_depth == 0:
                await self.on_progress("Agent finished")
            else:
                await self.on_progress("Sub-agent finished")
        except Exception as e:
            logger.error(f"Error in on_chat_model_start: {e}")

    async def _persist_stream_state(self, html_content=None):
        """Persist stream state to database for recovery and resume continuity."""
        from nova.models.Task import Task
        try:
            if html_content is None:
                if self._last_stream_html is not None:
                    html_content = self._last_stream_html
                elif self.final_chunks:
                    html_content = self._render_current_response()
            streamed_markdown = self.get_streamed_markdown()
            await sync_to_async(
                Task.objects.filter(id=self.task_id).update,
                thread_sensitive=False
            )(
                current_response=html_content,
                streamed_markdown=streamed_markdown,
                updated_at=timezone.now(),
            )
            self._last_runtime_touch_at = monotonic()
        except Exception as e:
            logger.error(f"Error persisting stream state: {e}")

    async def _touch_task_runtime(self, *, force: bool = False):
        from nova.models.Task import Task

        now = monotonic()
        if (
            not force
            and self._last_runtime_touch_at is not None
            and (now - self._last_runtime_touch_at) < self._runtime_touch_interval_seconds
        ):
            return
        try:
            await sync_to_async(
                Task.objects.filter(id=self.task_id).update,
                thread_sensitive=False,
            )(updated_at=timezone.now())
            self._last_runtime_touch_at = now
        except Exception as e:
            logger.error(f"Error updating task runtime heartbeat: {e}")

    def _append_segment_break_before_token(self, token: str) -> None:
        """Add a markdown paragraph break when the next segment starts."""
        if not self.final_chunks:
            return
        # If next token already starts with a newline, keep model formatting.
        if token.startswith("\n"):
            return
        current = ''.join(self.final_chunks)
        if not current:
            return
        if current.endswith("\n\n"):
            return
        if current.endswith("\n"):
            self.final_chunks.append("\n")
        else:
            self.final_chunks.append("\n\n")
        self._stream_has_pending_changes = True

    def _queue_push_notification(self, *, status: str) -> None:
        if (
            not self.push_notifications_enabled
            or not self.user_id
            or not bool(getattr(settings, "WEBPUSH_ENABLED", False))
        ):
            return
        try:
            from nova.tasks.notification_tasks import send_task_webpush_notification

            send_task_webpush_notification.delay(
                user_id=self.user_id,
                task_id=str(self.task_id),
                thread_id=self.thread_id,
                thread_mode=self.thread_mode,
                status=status,
            )
        except Exception:
            logger.exception(
                "Unable to enqueue WebPush notification for task_id=%s status=%s",
                self.task_id,
                status,
            )
