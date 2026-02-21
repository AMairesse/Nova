# nova/llm/agent_middleware.py
"""
Agent middleware system for Nova.

Provides hooks for agent-level operations like summarization,
context management, and other cross-cutting concerns.
"""
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field


@dataclass
class AgentContext:
    """Context passed to agent middleware."""
    agent_config: Any  # AgentConfig
    user: Any  # User model
    thread: Any  # Thread model
    progress_handler: Any = None  # TaskProgressHandler for real-time updates
    tool_prompt_hints: list[str] = field(default_factory=list)
    skill_catalog: dict[str, dict] = field(default_factory=dict)
    skill_control_tool_names: list[str] = field(default_factory=list)
    active_skill_ids: list[str] = field(default_factory=list)


class AgentMiddleware(ABC):
    """
    Base class for agent middleware.

    Middleware can hook into different points of agent execution:
    - before_message: Before processing a user message
    - after_message: After processing a message (successful or failed)
    - before_tool_call: Before executing a tool
    - after_tool_call: After executing a tool
    """

    @abstractmethod
    async def before_message(self, context: AgentContext, message: Any) -> None:
        """Called before processing a user message."""
        pass

    @abstractmethod
    async def after_message(self, context: AgentContext, result: Any) -> None:
        """Called after processing a message."""
        pass

    @abstractmethod
    async def before_tool_call(self, context: AgentContext, tool_call: Any) -> None:
        """Called before executing a tool."""
        pass

    @abstractmethod
    async def after_tool_call(self, context: AgentContext, tool_result: Any) -> None:
        """Called after executing a tool."""
        pass


class BaseAgentMiddleware(AgentMiddleware):
    """Base implementation with no-op methods."""

    async def before_message(self, context: AgentContext, message: Any) -> None:
        pass

    async def after_message(self, context: AgentContext, result: Any) -> None:
        pass

    async def before_tool_call(self, context: AgentContext, tool_call: Any) -> None:
        pass

    async def after_tool_call(self, context: AgentContext, tool_result: Any) -> None:
        pass
