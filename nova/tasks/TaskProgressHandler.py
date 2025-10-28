# nova/tasks/TaskProgressHandler.py
import logging
from uuid import UUID
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import AsyncCallbackHandler

from nova.utils import markdown_to_html

logger = logging.getLogger(__name__)


# Custom callback handler for synthesis and streaming
class TaskProgressHandler(AsyncCallbackHandler):
    def __init__(self, task_id, channel_layer):
        self.task_id = task_id
        self.channel_layer = channel_layer
        self.final_chunks = []
        self.current_tool = None
        self.tool_depth = 0
        self.token_count = 0

    async def publish_update(self, message_type, data):
        await self.channel_layer.group_send(
            f'task_{self.task_id}',
            {'type': 'task_update', 'message': {'type': message_type, **data}}
        )

    async def on_chain_start(self, serialized: Dict[str, Any],
                             inputs: Dict[str, Any], *, run_id: UUID,
                             parent_run_id: Optional[UUID] = None,
                             tags: Optional[List[str]] = None,
                             metadata: Optional[Dict[str, Any]] = None,
                             **kwargs: Any) -> None:
        pass

    async def on_chain_end(self, outputs: Dict[str, Any], *,
                           run_id: UUID, parent_run_id: Optional[UUID] = None,
                           tags: Optional[List[str]] = None,
                           **kwargs: Any) -> None:
        pass

    async def on_llm_start(self, serialized: Dict[str, Any],
                           prompts: List[str], **kwargs: Any):
        """Unified LLM start handler for both chat and completion models."""
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent started"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log': "Sub-agent started"})
        except Exception as e:
            logger.error(f"Error in on_llm_start: {e}")

    async def on_llm_new_token(self, token: str, *, run_id: UUID,
                               parent_run_id: Optional[UUID] = None,
                               **kwargs: Any) -> Any:
        try:
            # Send only chunks from the root run
            if self.tool_depth == 0:
                self.final_chunks.append(token)
                full_response = ''.join(self.final_chunks)
                clean_html = markdown_to_html(full_response)
                await self.publish_update('response_chunk',
                                          {'chunk': clean_html})
            else:
                # If a sub agent is generating a response,
                # send it as a progress update every 100 tokens
                self.token_count += 1
                if self.token_count % 100 == 0:
                    await self.publish_update('progress_update',
                                              {'progress_log':
                                               "Sub-agent still working..."})
        except Exception as e:
            logger.error(f"Error in on_llm_new_token: {e}")

    async def on_llm_end(self, response: Any, *, run_id: UUID,
                         parent_run_id: Optional[UUID] = None,
                         **kwargs: Any) -> Any:
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent finished"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent finished"})
        except Exception as e:
            logger.error(f"Error in on_llm_end: {e}")

    async def on_tool_start(self, serialized: Dict[str, Any],
                            input_str: str, *, run_id: UUID,
                            parent_run_id: Optional[UUID] = None,
                            tags: Optional[List[str]] = None,
                            metadata: Optional[Dict[str, Any]] = None,
                            **kwargs: Any) -> Any:
        try:
            # If a tool is starting,
            # store it to avoid sending response chunks back to the user
            tool_name = serialized.get('name', 'Unknown')
            self.current_tool = tool_name
            self.tool_depth += 1
            await self.publish_update('progress_update',
                                      {'progress_log':
                                       f"Tool '{tool_name}' started"})
        except Exception as e:
            logger.error(f"Error in on_tool_start: {e}")

    async def on_tool_end(self, output: Any, *, run_id: UUID,
                          parent_run_id: Optional[UUID] = None,
                          **kwargs: Any) -> Any:
        try:
            await self.publish_update('progress_update',
                                      {'progress_log':
                                       f"Tool '{self.current_tool}' finished"})
            # If a tool is ending, reset the current tool so that we may
            # send response chunks if the main agent is generating
            self.current_tool = None
            self.tool_depth -= 1
        except Exception as e:
            logger.error(f"Error in on_tool_end: {e}")

    async def on_agent_finish(self, finish: Any, *, run_id: UUID,
                              parent_run_id: Optional[UUID] = None,
                              **kwargs: Any) -> Any:
        try:
            if self.tool_depth == 0:
                await self.publish_update('progress_update',
                                          {'progress_log': "Agent finished"})
            else:
                await self.publish_update('progress_update',
                                          {'progress_log':
                                           "Sub-agent finished"})
        except Exception as e:
            logger.error(f"Error in on_chat_model_start: {e}")
