# nova/llm/llm_tools.py
import logging
import re
from typing import List
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

async def load_tools(instance) -> List[StructuredTool]:
    """
    Load and initialize tools associated with the agent instance.
    Returns a list of Langchain-ready tools.
    """
    tools = []
    loaded_builtin_modules = []

    # Load builtin tools (pre-fetched)
    for tool_obj in instance.builtin_tools:
        try:
            from nova.tools import import_module
            module = import_module(tool_obj.python_path)
            if not module:
                logger.warning(f"Failed to import module for builtin tool: {tool_obj.python_path}")
                continue

            # Call init if available (async)
            if hasattr(module, 'init'):
                await module.init(instance)

            # Get tools (new signature, await in case async)
            loaded_tools = await module.get_functions(tool=tool_obj, agent=instance)

            # Add to list
            tools.extend(loaded_tools)

            # Track module for cleanup
            loaded_builtin_modules.append(module)
        except Exception as e:
            logger.error(f"Error loading builtin tool {tool_obj.tool_subtype}: {str(e)}")
    
    instance._loaded_builtin_modules = loaded_builtin_modules  # Update instance tracker

    # Load MCP tools (pre-fetched data: (tool, cred, func_metas, cred_user_id))
    for tool_obj, cred, cached_func_metas, cred_user_id in instance.mcp_tools_data:
        try:
            from nova.mcp.client import MCPClient
            client = MCPClient(
                endpoint=tool_obj.endpoint, 
                thread_id=instance.thread_id,
                credential=cred, 
                transport_type=tool_obj.transport_type,
                user_id=cred_user_id
            )

            # Use pre-fetched or fetch via client
            if cached_func_metas is not None:
                func_metas = cached_func_metas
            else:
                func_metas = client.list_tools(force_refresh=True)

            for meta in func_metas:
                func_name = meta["name"]
                input_schema = meta.get("input_schema", {})
                description = meta.get("description", "")

                # ---------- safe factory captures current func_name & client -----------
                def _remote_call_factory(_name: str, _client: MCPClient):
                    async def _remote_call_async(**kwargs):
                        return await _client.acall(_name, **kwargs)

                    def _remote_call_sync(**kwargs):
                        return _client.call(_name, **kwargs)

                    return _remote_call_sync, _remote_call_async
                # -----------------------------------------------------------------------

                sync_f, async_f = _remote_call_factory(func_name, client)

                wrapped = StructuredTool.from_function(
                    func=sync_f,
                    coroutine=async_f,
                    name=re.sub(r"[^a-zA-Z0-9_-]+", "_", func_name)[:64],
                    description=description,
                    args_schema=None if input_schema == {} else input_schema,
                )
                tools.append(wrapped)

        except Exception as e:
            logger.warning(f"Failed to load MCP tools from {tool_obj.endpoint}: {str(e)}")

    # Load agents used as tools (pre-fetched)
    if instance.has_agent_tools:
        from nova.tools.agent_tool_wrapper import AgentToolWrapper

        for agent_tool in instance.agent_tools:
            wrapper = AgentToolWrapper(
                agent_tool, 
                instance.user,
                parent_config=instance._parent_config
            )
            langchain_tool = wrapper.create_langchain_tool()
            tools.append(langchain_tool)

    # Load files support tools
    from nova.tools import files
    file_tools = await files.get_functions(instance)
    tools.extend(file_tools)

    return tools
