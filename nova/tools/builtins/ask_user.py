# nova/tools/ask_user.py
from functools import partial
from typing import Optional, Dict, Any
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from django.utils.translation import gettext_lazy as _

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool

METADATA = {
    'name': 'Ask user',
    'description': 'Allow the agent to ask a question to the end user',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


async def _ask_user(agent: LLMAgent, question: str, schema: Optional[Dict[str, Any]] = None,
                    agent_name: Optional[str] = None) -> str:
    """
    Ask a blocking question to the end user.
    """
    # Interrupt execution flow
    response = interrupt({
        "action": "ask_user",
        "question": question,
        "schema": schema or {},
        "agent_name": agent_name or agent.agent_config.name,
    })

    # Return user response
    if response.get("action") == "user_response":
        user_response = response.get("user_response", "")
        return f"User response: {user_response}"

    return "User did not answer"


async def get_functions(tool: Tool, agent: LLMAgent) -> list[StructuredTool]:
    """Expose ask_user as a single StructuredTool, loaded unconditionally."""
    return [
        StructuredTool.from_function(
            func=None,
            coroutine=partial(_ask_user, agent),
            name="ask_user",
            description=_(
                "Ask the end-user a clarification question and pause execution. "
                "Use when additional information is required to proceed."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to show to the user",
                    },
                    "schema": {
                        "type": "object",
                        "description": "Optional JSON schema describing expected answer shape",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "Optional name of the agent that asked the question",
                    }
                },
                "required": ["question"],
            },
        )
    ]
