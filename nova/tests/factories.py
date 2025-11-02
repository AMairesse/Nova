from __future__ import annotations

import itertools
from typing import Iterable, Optional

from django.contrib.auth import get_user_model

from nova.models.AgentConfig import AgentConfig
from nova.models.Message import Actor, Message
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserFile import UserFile


__all__ = [
    "unique_string",
    "create_user",
    "create_provider",
    "create_agent",
    "create_thread",
    "create_message",
    "create_tool",
    "create_tool_credential",
    "create_task",
    "create_user_file",
]


_counter = itertools.count()


def unique_string(prefix: str = "obj") -> str:
    """Generate a deterministic unique string for use in tests."""
    return f"{prefix}-{next(_counter)}"


def create_user(
    *,
    username: Optional[str] = None,
    password: str = "testpass123",
    email: Optional[str] = None,
    **extra,
):
    User = get_user_model()
    if username is None:
        username = unique_string("user")
    if email is None:
        email = f"{username}@example.com"
    user = User.objects.create_user(username=username, email=email, password=password, **extra)
    return user


def create_provider(
    user,
    *,
    name: Optional[str] = None,
    provider_type: ProviderType = ProviderType.OPENAI,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = "dummy-key",
    base_url: Optional[str] = None,
    max_context_tokens: int = 4096,
    **extra,
) -> LLMProvider:
    defaults = {
        "name": name or unique_string("provider"),
        "provider_type": provider_type,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "max_context_tokens": max_context_tokens,
    }
    defaults.update(extra)
    return LLMProvider.objects.create(user=user, **defaults)


def create_agent(
    user,
    *,
    provider: Optional[LLMProvider] = None,
    name: Optional[str] = None,
    system_prompt: str = "You are a helpful assistant.",
    is_tool: bool = False,
    tool_description: Optional[str] = None,
    recursion_limit: int = 25,
    tools: Optional[Iterable[Tool]] = None,
    **extra,
) -> AgentConfig:
    if provider is None:
        provider = create_provider(user)
    defaults = {
        "name": name or unique_string("agent"),
        "llm_provider": provider,
        "system_prompt": system_prompt,
        "is_tool": is_tool,
        "tool_description": tool_description if is_tool else None,
        "recursion_limit": recursion_limit,
    }
    defaults.update(extra)
    agent = AgentConfig.objects.create(user=user, **defaults)
    if tools:
        agent.tools.set(tools)
    return agent


def create_thread(
    user,
    *,
    subject: Optional[str] = None,
    **extra,
) -> Thread:
    defaults = {
        "subject": subject or unique_string("thread"),
    }
    defaults.update(extra)
    return Thread.objects.create(user=user, **defaults)


def create_message(
    thread: Thread,
    *,
    user=None,
    text: str = "Hello world",
    actor: Actor = Actor.USER,
    internal_data: Optional[dict] = None,
    **extra,
) -> Message:
    message = Message.objects.create(
        user=user or thread.user,
        thread=thread,
        text=text,
        actor=actor,
        internal_data=internal_data or {},
        **extra,
    )
    return message


def create_tool(
    user,
    *,
    name: Optional[str] = None,
    tool_type: Tool.ToolType = Tool.ToolType.API,
    description: str = "Test tool",
    endpoint: Optional[str] = "https://api.example.com/v1",
    tool_subtype: Optional[str] = None,
    is_active: bool = True,
    python_path: Optional[str] = "",
    **extra,
) -> Tool:
    defaults = {
        "name": name or unique_string("tool"),
        "description": description,
        "tool_type": tool_type,
        "endpoint": endpoint if tool_type in {Tool.ToolType.API, Tool.ToolType.MCP} else None,
        "tool_subtype": tool_subtype,
        "is_active": is_active,
        "python_path": (
            python_path
            or (
                f"nova.tools.builtins.{tool_subtype}"
                if tool_type == Tool.ToolType.BUILTIN and tool_subtype
                else ""
            )
        ),
    }
    defaults.update(extra)
    return Tool.objects.create(user=user, **defaults)


def create_tool_credential(
    user,
    tool: Tool,
    *,
    auth_type: str = "basic",
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    config: Optional[dict] = None,
    **extra,
) -> ToolCredential:
    defaults = {
        "auth_type": auth_type,
        "username": username,
        "password": password,
        "token": token,
        "config": config or {},
    }
    defaults.update(extra)
    return ToolCredential.objects.create(user=user, tool=tool, **defaults)


def create_task(
    user,
    thread: Thread,
    *,
    agent: Optional[AgentConfig] = None,
    status: TaskStatus = TaskStatus.PENDING,
    **extra,
) -> Task:
    defaults = {
        "agent": agent,
        "status": status,
    }
    defaults.update(extra)
    return Task.objects.create(user=user, thread=thread, **defaults)


def create_user_file(
    thread: Thread,
    *,
    user=None,
    key: Optional[str] = None,
    original_filename: str = "document.txt",
    mime_type: str = "text/plain",
    size: int = 10,
    **extra,
) -> UserFile:
    defaults = {
        "key": key or f"{thread.user_id}/{thread.id}/{unique_string('file')}",
        "original_filename": original_filename,
        "mime_type": mime_type,
        "size": size,
    }
    defaults.update(extra)
    return UserFile.objects.create(
        user=user or thread.user,
        thread=thread,
        **defaults,
    )