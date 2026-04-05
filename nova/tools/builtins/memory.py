"""Nova builtin capability: Memory mount for React Terminal v2."""

from __future__ import annotations

from typing import List


METADATA = {
    "name": "Memory",
    "description": "Expose a user-scoped /memory mount in the React Terminal runtime.",
    "requires_config": False,
    "config_fields": [],
    "test_function": None,
    "test_function_args": [],
}


def get_prompt_instructions() -> List[str]:
    return []


async def get_functions(tool, agent):
    del tool, agent
    return []
