# nova/llm/llm_tools.py
import logging
import re
from typing import List
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


async def load_tools(agent) -> List[StructuredTool]:
    """
    Load and initialize tools associated with the agent.
    Returns a list of Langchain-ready tools.
    """
    tools = []
    loaded_builtin_modules = []

    # Load builtin tools (pre-fetched)
    for tool_obj in agent.builtin_tools:
        try:
            from nova.tools import import_module
            module = import_module(tool_obj.python_path)
            if not module:
                logger.warning(f"Failed to import module for builtin tool: {tool_obj.python_path}")
                continue

            # Call init if available (async)
            if hasattr(module, 'init'):
                await module.init(agent)

            # Get tools (new signature, await in case async)
            loaded_tools = await module.get_functions(tool=tool_obj, agent=agent)

            # Add to list
            tools.extend(loaded_tools)

            # Track module for cleanup
            loaded_builtin_modules.append(module)
        except Exception as e:
            logger.error(f"Error loading builtin tool {tool_obj.tool_subtype}: {str(e)}")

    agent._loaded_builtin_modules = loaded_builtin_modules

    # Load MCP tools (pre-fetched data: (tool, cred, func_metas, cred_user_id))
    for tool_obj, cred, cached_func_metas, cred_user_id in agent.mcp_tools_data:
        try:
            from nova.mcp.client import MCPClient
            client = MCPClient(
                endpoint=tool_obj.endpoint,
                credential=cred,
                transport_type=tool_obj.transport_type,
                user_id=cred_user_id
            )

            # Use pre-fetched or fetch via client
            if cached_func_metas is not None:
                func_metas = cached_func_metas
            else:
                func_metas = await client.alist_tools(force_refresh=True)

            for meta in func_metas:
                func_name = meta["name"]
                input_schema = meta.get("input_schema", {})
                description = meta.get("description", "")

                # ---------- safe factory captures current func_name & client -----------
                def _remote_call_factory(_name: str, _client: MCPClient):
                    async def _remote_call_async(**kwargs):
                        return await _client.acall(_name, **kwargs)

                    return _remote_call_async
                # -----------------------------------------------------------------------

                async_f = _remote_call_factory(func_name, client)

                # Sanitize tool name: replace invalid characters with underscores
                sanitized_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", func_name).strip("_")[:64]
                # Ensure name is not empty and starts with a letter
                if not sanitized_name or not sanitized_name[0].isalpha():
                    sanitized_name = f"tool_{hash(func_name) % 10000}"

                wrapped = StructuredTool.from_function(
                    func=None,
                    coroutine=async_f,
                    name=sanitized_name,
                    description=description,
                    args_schema=None if input_schema == {} else input_schema,
                )
                tools.append(wrapped)

        except Exception as e:
            logger.warning(f"Failed to load MCP tools from {tool_obj.endpoint}: {str(e)}")

    # Load agents used as tools (pre-fetched)
    if agent.has_agent_tools:
        from nova.tools.agent_tool_wrapper import AgentToolWrapper

        parent_callbacks = agent.config.get('callbacks', [])

        for agent_config in agent.agent_tools:
            wrapper = AgentToolWrapper(
                agent_config=agent_config,
                thread=agent.thread,
                user=agent.user,
                parent_callbacks=parent_callbacks,
            )
            langchain_tool = wrapper.create_langchain_tool()
            tools.append(langchain_tool)

    # Load files support tools
    from nova.tools import files
    file_tools = await files.get_functions(agent)
    tools.extend(file_tools)

    return tools
