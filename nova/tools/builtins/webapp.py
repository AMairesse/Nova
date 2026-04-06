# nova/tools/builtins/webapp.py
from langchain_core.tools import StructuredTool
from typing import List


METADATA = {
    "name": "WebApp",
    "description": "Expose a live static webapp from a terminal source directory.",
    "loading": {
        "mode": "skill",
        "skill_id": "webapp",
        "skill_label": "WebApp",
    },
    "requires_config": False,
    "config_fields": [],
    "test_function": None,
    "test_function_args": [],
}


async def get_functions(tool, agent) -> List[StructuredTool]:
    # WebApp is terminal-native in the v2 runtime and no longer exposes legacy
    # callable tools to LangChain agents.
    return []


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    del agent, tools
    return [
        "WebApp publishing is terminal-native in React Terminal v2. Build files in the filesystem, then use `webapp expose` from the terminal runtime."
    ]
