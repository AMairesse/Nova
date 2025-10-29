# nova/tools/ask_user.py
from __future__ import annotations

from typing import Optional, Dict, Any
from django.utils.translation import gettext_lazy as _
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from nova.llm.llm_agent import LLMAgent
from functools import partial


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


async def get_functions(agent: LLMAgent) -> list[StructuredTool]:
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
