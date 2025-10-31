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

    async def on_interrupt(self, interaction_id, question, schema, agent_name):
        '''
        Send a message to the client when the task is interrupted
        '''
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

    async def on_error(self, error_msg, error_category):
        '''
        Send a message to the client when an error occurs
        '''
        await self.publish_update('task_error', {'error': error_msg, 'category': error_category})

    async def on_progress(self, message):
        '''
        Send a message to the client when the task is in progress
        '''
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

    async def on_new_message(self, id, text, actor, internal_data, created_at):
        await self.publish_update('new_message', {'message': {'id': id,
                                                              'text': text,
                                                              'actor': actor,
                                                              'internal_data': internal_data,
                                                              'created_at': created_at
                                                              }})

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
                self.final_chunks.append(token)
                full_response = ''.join(self.final_chunks)
                clean_html = markdown_to_html(full_response)
                await self.on_chunk(clean_html)
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
                await self.on_progress("Agent finished")
            else:
                await self.on_progress("Sub-agent finished")
        except Exception as e:
            logger.error(f"Error in on_llm_end: {e}")

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
