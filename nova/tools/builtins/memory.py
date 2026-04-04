"""Nova builtin tool: Memory (v2)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool

from nova.memory.service import (
    add_memory_item,
    archive_memory_item,
    get_default_memory_theme_slug,
    get_memory_item,
    list_themes_for_user,
    normalize_memory_theme_slug,
    search_memory_items,
)
from nova.llm.llm_agent import LLMAgent

METADATA = {
    "name": "Memory",
    "description": "Access and manage structured long-term memory (search + add + get).",
    "requires_config": False,
    "config_fields": [],
    "test_function": None,
    "test_function_args": [],
}


def get_prompt_instructions() -> List[str]:
    return [
        "Use memory_search when you need user-specific facts/preferences not guaranteed in current context.",
        "Use memory_get to read a specific memory item in full before relying on it.",
        "Use memory_add for durable user preferences/facts that should persist across conversations.",
    ]


def _normalize_theme_slug(theme: str) -> str:
    return normalize_memory_theme_slug(theme)


def _get_default_theme_slug() -> str:
    return get_default_memory_theme_slug()


async def list_themes(agent: LLMAgent, status: Optional[str] = None) -> Dict[str, Any]:
    return await list_themes_for_user(user=agent.user, status=status)


async def add(
    type: str,
    content: str,
    agent: LLMAgent,
    theme: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return await add_memory_item(
        user=agent.user,
        item_type=type,
        content=content,
        theme=theme,
        tags=tags,
        source_thread=getattr(agent, "thread", None),
        source_message=None,
        allow_empty=False,
    )


async def get(item_id: int, agent: LLMAgent) -> Dict[str, Any]:
    return await get_memory_item(item_id=item_id, user=agent.user)


async def archive(item_id: int, agent: LLMAgent) -> Dict[str, Any]:
    return await archive_memory_item(item_id=item_id, user=agent.user)


async def search(
    query: str,
    agent: LLMAgent,
    limit: int = 10,
    theme: Optional[str] = None,
    types: Optional[List[str]] = None,
    recency_days: Optional[int] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    return await search_memory_items(
        query=query,
        user=agent.user,
        limit=limit,
        theme=theme,
        types=types,
        recency_days=recency_days,
        status=status,
    )


async def get_functions(tool, agent: LLMAgent):
    return [
        StructuredTool.from_function(
            coroutine=lambda query, limit=10, theme=None, types=None, recency_days=None, status=None: search(
                query=query,
                limit=limit,
                theme=theme,
                types=types,
                recency_days=recency_days,
                status=status,
                agent=agent,
            ),
            name="memory_search",
            description="Search long-term memory items relevant to a query",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (empty or * will select all)"},
                    "limit": {"type": "integer", "description": "Max results (1-50)", "default": 10},
                    "theme": {"type": "string", "description": "Optional theme slug"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of memory item types",
                    },
                    "recency_days": {
                        "type": "integer",
                        "description": "Optional: only items from the last N days",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional: filter by status (active|archived|any)",
                    },
                },
                "required": ["query"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda type, content, theme=None, tags=None: add(
                type=type,
                content=content,
                theme=theme,
                tags=tags,
                agent=agent,
            ),
            name="memory_add",
            description="Add a long-term memory item",
            args_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Memory item type (preference|fact|instruction|summary|other)",
                    },
                    "content": {"type": "string", "description": "Memory content"},
                    "theme": {"type": "string", "description": "Optional theme"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags",
                    },
                },
                "required": ["type", "content"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda item_id: get(item_id, agent),
            name="memory_get",
            description="Get a memory item by id",
            args_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Memory item id"},
                },
                "required": ["item_id"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda status=None: list_themes(agent=agent, status=status),
            name="memory_list_themes",
            description="List themes in long-term memory",
            args_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional: filter by associated item status (active|archived|any)",
                    },
                },
                "required": [],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda item_id: archive(item_id=item_id, agent=agent),
            name="memory_archive",
            description="Archive (soft-delete) a memory item by id",
            args_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Memory item id"},
                },
                "required": ["item_id"],
            },
        ),
    ]

