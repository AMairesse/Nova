# nova/llm/llm_tools.py
import logging
import re
import inspect
from typing import List
from langchain_core.tools import StructuredTool
from nova.llm.skill_policy import (
    TOOL_SKILL_CONTROL_ATTR,
    SkillLoadingPolicy,
    apply_skill_policy_to_tool,
    get_module_skill_policy,
)
from nova.llm.skill_runtime_tools import build_skill_control_tools

logger = logging.getLogger(__name__)


def _dedupe_tool_names(tools: list[StructuredTool]) -> list[StructuredTool]:
    """Ensure tool names are globally unique for LangGraph ToolNode indexing."""
    seen: set[str] = set()
    max_len = 64

    for idx, tool in enumerate(tools, start=1):
        original = (getattr(tool, "name", "") or "").strip() or f"tool_{idx}"
        candidate = original
        final_name = original

        if candidate in seen:
            counter = 2
            while True:
                suffix = f"__dup{counter}"
                base = original[: max(1, max_len - len(suffix))]
                candidate = f"{base}{suffix}"
                if candidate not in seen:
                    break
                counter += 1

            logger.warning(
                "Duplicate tool name '%s' detected; renaming to '%s'.",
                original,
                candidate,
            )
            try:
                tool.name = candidate
                final_name = candidate
            except Exception:
                logger.warning(
                    "Could not set tool name on %s; duplicate name '%s' may remain.",
                    type(tool).__name__,
                    original,
                )
        else:
            final_name = candidate

        seen.add(final_name)

    return tools


async def load_tools(agent) -> List[StructuredTool]:
    """
    Load and initialize tools associated with the agent.
    Returns a list of Langchain-ready tools.
    """
    tools = []
    loaded_builtin_modules = []
    collected_prompt_hints: list[str] = []
    skill_catalog: dict[str, dict] = {}
    skill_instructions_loaded_for: set[str] = set()

    def _ensure_skill_entry(policy: SkillLoadingPolicy) -> dict:
        return skill_catalog.setdefault(
            policy.skill_id,
            {
                "id": policy.skill_id,
                "label": policy.skill_label or policy.skill_id,
                "tool_names": [],
                "instructions": [],
            },
        )

    async def _collect_skill_instructions(module, *, grouped_tools, policy: SkillLoadingPolicy):
        if not policy.is_skill:
            return

        module_name = getattr(module, "__name__", repr(module))
        dedupe_key = f"{module_name}:{policy.skill_id}"
        if dedupe_key in skill_instructions_loaded_for:
            return
        skill_instructions_loaded_for.add(dedupe_key)

        provider = getattr(module, "get_skill_instructions", None)
        if not callable(provider):
            return

        try:
            instructions = provider(agent=agent, tools=grouped_tools)
            if inspect.isawaitable(instructions):
                instructions = await instructions
        except Exception as e:
            logger.warning(
                "Could not load skill instructions from %s (%s): %s",
                module_name,
                policy.skill_id,
                str(e),
            )
            return

        if isinstance(instructions, str):
            instructions = [instructions]

        entry = _ensure_skill_entry(policy)
        for instruction in instructions or []:
            text = str(instruction or "").strip()
            if text and text not in entry["instructions"]:
                entry["instructions"].append(text)

    def _tag_loaded_tools_as_skill(loaded_tools: list[StructuredTool], policy: SkillLoadingPolicy):
        for loaded_tool in loaded_tools:
            apply_skill_policy_to_tool(loaded_tool, policy)

            if not policy.is_skill:
                continue

            entry = _ensure_skill_entry(policy)
            tool_name = str(getattr(loaded_tool, "name", "") or "").strip()
            if tool_name and tool_name not in entry["tool_names"]:
                entry["tool_names"].append(tool_name)

    def _append_prompt_hints(hints):
        if not hints:
            return
        if isinstance(hints, str):
            hints = [hints]
        for hint in hints:
            text = str(hint or "").strip()
            if text and text not in collected_prompt_hints:
                collected_prompt_hints.append(text)

    def _append_skill_prompt_hints(policy: SkillLoadingPolicy, hints):
        if not policy.is_skill:
            return
        if isinstance(hints, str):
            hints = [hints]
        entry = _ensure_skill_entry(policy)
        for hint in hints or []:
            text = str(hint or "").strip()
            if text and text not in entry["instructions"]:
                entry["instructions"].append(text)

    def _route_prompt_hints(hints, *, policy: SkillLoadingPolicy):
        if not hints:
            return
        if policy.is_skill:
            _append_skill_prompt_hints(policy, hints)
            return
        _append_prompt_hints(hints)

    async def _collect_prompt_hints(module, *, grouped_tools=None, policy: SkillLoadingPolicy):
        try:
            if grouped_tools is not None:
                provider = getattr(module, "get_aggregated_prompt_instructions", None)
                if callable(provider):
                    hints = provider(tools=grouped_tools, agent=agent)
                    if inspect.isawaitable(hints):
                        hints = await hints
                    _route_prompt_hints(hints, policy=policy)
                    return

            provider = getattr(module, "get_prompt_instructions", None)
            if not callable(provider):
                return
            hints = provider()
            if inspect.isawaitable(hints):
                hints = await hints
            _route_prompt_hints(hints, policy=policy)
        except Exception as e:
            logger.debug(f"Skipping prompt instructions for module {getattr(module, '__name__', module)}: {e}")

    # Filter builtin tools by policy first, then group by python_path for optional aggregation.
    builtin_groups: dict[str, list] = {}
    for tool_obj in agent.builtin_tools:
        try:
            # ------------------------------------------------------------------
            # Continuous discussion policy:
            # - `conversation.*` tools are reserved to the *main* agent.
            # - Sub-agents are agents used as tools (AgentConfig.is_tool=True).
            # - Only expose conversation tools when the backing thread is in
            #   continuous mode.
            # ------------------------------------------------------------------
            try:
                if getattr(tool_obj, "tool_subtype", None) == "conversation":
                    thread_mode = getattr(getattr(agent, "thread", None), "mode", None)
                    is_sub_agent = bool(getattr(getattr(agent, "agent_config", None), "is_tool", False))
                    if thread_mode != "continuous" or is_sub_agent:
                        continue
            except Exception:
                # Be conservative: if we cannot assert policy, don't expose.
                if getattr(tool_obj, "tool_subtype", None) == "conversation":
                    continue

            python_path = getattr(tool_obj, "python_path", "") or ""
            if not python_path:
                logger.warning("Builtin tool %s has no python_path; skipping.", getattr(tool_obj, "id", "unknown"))
                continue
            builtin_groups.setdefault(python_path, []).append(tool_obj)
        except Exception as e:
            logger.error(f"Error preparing builtin tool {getattr(tool_obj, 'tool_subtype', 'unknown')}: {str(e)}")

    from nova.tools import import_module

    for python_path, grouped_tools in builtin_groups.items():
        module = import_module(python_path)
        if not module:
            logger.warning(f"Failed to import module for builtin tool: {python_path}")
            continue
        module_skill_policy = get_module_skill_policy(module)

        try:
            # Call init once per module if available.
            if hasattr(module, 'init'):
                await module.init(agent)

            aggregation_used = False
            aggregate_provider = getattr(module, "get_aggregated_functions", None)
            if callable(aggregate_provider):
                spec = getattr(module, "AGGREGATION_SPEC", {}) or {}
                min_instances = spec.get("min_instances", 2)
                try:
                    min_instances = max(1, int(min_instances))
                except (TypeError, ValueError):
                    min_instances = 2

                if len(grouped_tools) >= min_instances:
                    try:
                        loaded_tools = await aggregate_provider(
                            tools=grouped_tools,
                            agent=agent,
                        )
                        _tag_loaded_tools_as_skill(loaded_tools, module_skill_policy)
                        tools.extend(loaded_tools)
                        aggregation_used = True
                    except Exception as e:
                        logger.error(
                            "Error loading aggregated builtin tools for %s: %s",
                            python_path,
                            str(e),
                        )

            if aggregation_used:
                await _collect_prompt_hints(
                    module,
                    grouped_tools=grouped_tools,
                    policy=module_skill_policy,
                )
                await _collect_skill_instructions(
                    module,
                    grouped_tools=grouped_tools,
                    policy=module_skill_policy,
                )
            else:
                tool_loader = getattr(module, "get_functions", None)
                if not callable(tool_loader):
                    logger.warning(
                        "Builtin module %s has no get_functions() and aggregation was not used.",
                        python_path,
                    )
                    continue

                for tool_obj in grouped_tools:
                    try:
                        loaded_tools = await tool_loader(tool=tool_obj, agent=agent)
                        _tag_loaded_tools_as_skill(loaded_tools, module_skill_policy)
                        tools.extend(loaded_tools)
                    except Exception as e:
                        logger.error(f"Error loading builtin tool {tool_obj.tool_subtype}: {str(e)}")
                await _collect_prompt_hints(module, policy=module_skill_policy)
                await _collect_skill_instructions(
                    module,
                    grouped_tools=grouped_tools,
                    policy=module_skill_policy,
                )

            # Track module for cleanup once.
            loaded_builtin_modules.append(module)
        except Exception as e:
            logger.error("Error loading builtin module %s: %s", python_path, str(e))

    # ------------------------------------------------------------------
    # Continuous discussion policy (auto-attach conversation tools):
    # If the current run is the main agent on a continuous thread, ensure
    # `conversation_*` tools are available even when the conversation builtin
    # is not explicitly assigned in agent_config.tools.
    # ------------------------------------------------------------------
    try:
        thread_mode = getattr(getattr(agent, "thread", None), "mode", None)
        is_sub_agent = bool(getattr(getattr(agent, "agent_config", None), "is_tool", False))
        has_conversation_tools = any(
            getattr(t, "name", "") in {"conversation_search", "conversation_get"}
            for t in tools
        )

        if thread_mode == "continuous" and not is_sub_agent and not has_conversation_tools:
            from nova.continuous.tools import conversation_tools

            conversation_skill_policy = get_module_skill_policy(conversation_tools)
            loaded_tools = await conversation_tools.get_functions(tool=None, agent=agent)
            _tag_loaded_tools_as_skill(loaded_tools, conversation_skill_policy)
            tools.extend(loaded_tools)
            loaded_builtin_modules.append(conversation_tools)
            await _collect_prompt_hints(conversation_tools, policy=conversation_skill_policy)
    except Exception as e:
        logger.warning(f"Failed to auto-load conversation builtin tools: {e}")

    # Add skill control tools after builtins are loaded and skill catalog is known.
    tools.extend(build_skill_control_tools(skill_catalog))

    agent._loaded_builtin_modules = loaded_builtin_modules
    agent.tool_prompt_hints = collected_prompt_hints
    agent.skill_catalog = skill_catalog

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

        for agent_config in agent.agent_tools:
            wrapper = AgentToolWrapper(
                agent_config=agent_config,
                thread=agent.thread,
                user=agent.user,
            )
            langchain_tool = wrapper.create_langchain_tool()
            tools.append(langchain_tool)

    # Load files support tools
    from nova.tools import files

    files_skill_policy = get_module_skill_policy(files)
    file_tools = await files.get_functions(agent)
    _tag_loaded_tools_as_skill(file_tools, files_skill_policy)
    tools.extend(file_tools)
    if file_tools:
        await _collect_skill_instructions(
            files,
            grouped_tools=file_tools,
            policy=files_skill_policy,
        )
        await _collect_prompt_hints(files, policy=files_skill_policy)

    deduped_tools = _dedupe_tool_names(tools)
    agent.skill_control_tool_names = [
        str(getattr(tool, "name", "") or "").strip()
        for tool in deduped_tools
        if getattr(tool, TOOL_SKILL_CONTROL_ATTR, False)
    ]
    return deduped_tools
