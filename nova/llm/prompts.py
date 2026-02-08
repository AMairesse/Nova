# nova/llm/prompts.py
"""
Dynamic prompt middleware for Nova agents.
Replaces static string-based system prompt building with flexible middleware.
"""
import logging
from datetime import date
from typing import Optional

from langchain.agents.middleware import dynamic_prompt
from langchain.agents.middleware.types import ModelRequest
from django.db.models import Count, Q
from nova.models.UserFile import UserFile
from nova.models.Memory import MemoryTheme
from nova.models.Tool import Tool
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


@dynamic_prompt
async def nova_system_prompt(request: ModelRequest) -> str:
    """
    Dynamic system prompt that adapts based on agent configuration and context.

    This replaces the static string building in LLMAgent.build_system_prompt().
    """
    # Get agent config from runtime.context (AgentContext is nested inside runtime)
    # request.runtime is a langgraph.runtime.Runtime object
    # request.runtime.context is our AgentContext dataclass
    runtime_context = getattr(request.runtime, 'context', None) if request.runtime else None

    agent_config = getattr(runtime_context, 'agent_config', None)
    user = getattr(runtime_context, 'user', None)
    thread = getattr(runtime_context, 'thread', None)

    if not agent_config:
        # Fallback to basic prompt if no config available
        return "You are a helpful assistant."

    # Start with base system prompt
    base_prompt = ""
    if agent_config.system_prompt:
        base_prompt = agent_config.system_prompt
        # Handle {today} template
        today = date.today().strftime("%A %d of %B, %Y")
        if "{today}" in base_prompt:
            base_prompt = base_prompt.format(today=today)
    else:
        # Default prompt
        today = date.today().strftime("%A %d of %B, %Y")
        base_prompt = (
            f"You are a helpful assistant. Today is {today}. "
            "Be concise and direct. If you need to display "
            "structured information, use markdown."
        )

    # Check if memory tool is enabled and inject user memory
    memory_tool_enabled = await _is_memory_tool_enabled(agent_config)

    if memory_tool_enabled and user:
        try:
            user_memory = await _get_user_memory(user)
            if user_memory:
                base_prompt += user_memory
        except Exception as e:
            logger.warning(f"Failed to load user memory for dynamic prompt: {e}")

    # Add information about files available in discussion
    if thread and user:
        file_context = await _get_file_context(thread, user)
        if file_context:
            base_prompt += file_context
    elif not thread:
        # When no thread is associated (e.g. /api/ask/), skip DB access
        base_prompt += "\nNo attached files available.\n"

    # Add tool-owned usage hints collected during tool loading.
    tool_hints = _get_tool_prompt_hints(runtime_context)
    if tool_hints:
        base_prompt += "\n\nTool usage policy:\n"
        for hint in tool_hints:
            base_prompt += f"- {hint}\n"

    return base_prompt


def _get_tool_prompt_hints(runtime_context) -> list[str]:
    hints = list(getattr(runtime_context, "tool_prompt_hints", []) or [])
    out: list[str] = []
    for h in hints:
        s = str(h or "").strip()
        if s and s not in out:
            out.append(s)
    return out


async def _is_memory_tool_enabled(agent_config) -> bool:
    """Check if memory tool is enabled for this agent."""
    # Wrap ORM call in sync_to_async to avoid async context error
    tools = await sync_to_async(
        list, thread_sensitive=False
    )(agent_config.tools.filter(is_active=True, tool_type=Tool.ToolType.BUILTIN))
    return any(
        tool.tool_subtype == 'memory' and tool.is_active
        for tool in tools
    )


async def _get_user_memory(user) -> Optional[str]:
    """Return lightweight memory discovery hints.

    Memory v2 is tool-driven: do NOT inject memory content, only compact discovery.
    """
    try:
        # Include lightweight discovery hints: top themes + active item counts.
        # NOTE: keep this intentionally small to avoid prompt bloat.
        def _load_theme_hints():
            themes_qs = (
                MemoryTheme.objects.filter(user=user)
                .annotate(active_count=Count("items", filter=Q(items__status="active")))
                .order_by("-active_count", "slug")
            )
            return list(themes_qs.values_list("slug", "active_count"))

        # NOTE: SQLite (tests) is prone to table locking when ORM runs in a separate
        # worker thread. Keep DB access thread-sensitive.
        theme_hints = await sync_to_async(_load_theme_hints, thread_sensitive=True)()

        # Keep this block intentionally short to avoid prompt bloat.
        lines = ["\nLong-term memory themes available:\n"]

        if theme_hints:
            # Limit how many themes we list.
            shown = theme_hints[:10]
            suffix = "" if len(theme_hints) <= 10 else f" (+{len(theme_hints) - 10} more)"
            formatted = ", ".join([f"{slug} ({count})" for slug, count in shown])
            lines.append(f"\nKnown memory themes (active items): {formatted}{suffix}\n")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Failed to load memory themes: {e}")
        return "\nLong-term memory is available.\n"


async def _get_file_context(thread, user) -> Optional[str]:
    """Get file context information for the thread."""
    try:
        # Single DB round-trip to count files
        file_count = await sync_to_async(
            UserFile.objects.filter(thread=thread, user=user).count
        )()

        if file_count:
            return f"\n{file_count} file(s) are attached to this thread. Use file tools if needed."
        else:
            return "\nNo attached files available.\n"
    except Exception as e:
        logger.warning(f"Failed to get file context: {e}")
        return "\nNo attached files available.\n"
