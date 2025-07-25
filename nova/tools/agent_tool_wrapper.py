# nova/tools/agent_tool_wrapper.py
"""
Utility that exposes another `Agent` instance as a LangChain
`StructuredTool`, allowing agents to call each other as tools.

• All user-facing strings are wrapped in gettext for i18n.
• Comments are written in English only.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from django.utils.translation import gettext as _
from langchain_core.tools import StructuredTool

from ..llm_agent import LLMAgent
from ..models import Agent


class AgentToolWrapper:
    """
    Build a LangChain `StructuredTool` that forwards the question
    to the wrapped `Agent` and returns its answer.
    """

    def __init__(
        self,
        agent: Agent,
        parent_user,
        parent_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.agent = agent
        self.parent_user = parent_user
        self.parent_config: Dict[str, Any] = parent_config or {}

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #
    def create_langchain_tool(self) -> StructuredTool:
        """Return a `StructuredTool` ready to be injected into LangChain."""

        def execute_agent(question: str) -> str:
            """
            Inner callable executed by LangChain.
            Forwards the prompt to the wrapped agent and returns its answer.
            """

            # -------- Langfuse instrumentation ------------------------- #
            callbacks = self.parent_config.get("callbacks", [])
            for cb in callbacks:
                if getattr(cb, "trace", None):
                    try:
                        cb.trace.update(tags=[f"agent_tool_call:{self.agent.name}"])
                    except Exception:
                        # Ignore if Langfuse API signature changes
                        pass
            # ----------------------------------------------------------- #

            parent_thread_id = (
                self.parent_config.get("configurable", {}).get("thread_id")
            )

            agent_llm = LLMAgent(
                user=self.parent_user,
                thread_id=parent_thread_id,
                agent=self.agent,
                parent_config=self.parent_config,
            )

            return agent_llm.invoke(question)

        # ----------------------- Input schema --------------------------- #
        description = _(
            "Question or instruction sent to the agent %(name)s"
        ) % {"name": self.agent.name}

        input_schema = {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": description,
                }
            },
            "required": ["question"],
        }

        # ------------------------ Safe name ----------------------------- #
        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+", "_", f"agent_{self.agent.name.lower()}"
        )[:64]

        # ------------------ Tool description --------------------------- #
        tool_description = self.agent.tool_description

        return StructuredTool.from_function(
            func=execute_agent,
            name=safe_name,
            description=tool_description,
            args_schema=input_schema,
        )
