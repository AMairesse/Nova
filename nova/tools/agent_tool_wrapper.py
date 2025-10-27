# nova/tools/agent_tool_wrapper.py
"""
Utility that exposes another `Agent` instance as a LangChain
`StructuredTool`, allowing agents to call each other as tools.

• All user-facing strings are wrapped in gettext for i18n.
• Comments are written in English only.
"""
from __future__ import annotations

import re
from django.conf import settings
from django.utils.translation import gettext as _
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.models.models import Agent
from nova.models.Thread import Thread

import logging
logger = logging.getLogger(__name__)


class AgentToolWrapper:
    """
    Build a LangChain `StructuredTool` that forwards the question
    to the wrapped `Agent` and returns its answer.
    """

    def __init__(
        self,
        agent_config: Agent,
        thread: Thread,
        user: settings.AUTH_USER_MODEL,
        parent_callbacks=None,
        current_task=None,
    ) -> None:
        self.agent_config = agent_config
        self.thread = thread
        self.user = user
        self.parent_callbacks = parent_callbacks or []
        self.current_task = current_task

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #
    def create_langchain_tool(self) -> StructuredTool:
        """Return a `StructuredTool` ready to be injected into LangChain."""

        async def execute_agent(question: str) -> str:
            """
            Inner callable executed by LangChain.
            Forwards the prompt to the wrapped agent and returns its answer.
            """
            agent_llm = await LLMAgent.create(
                self.user,
                self.thread,
                self.agent_config,
                callbacks=self.parent_callbacks,  # propagate streaming callbacks
            )
            # Ensure ask_user has access to the same Task context
            if self.current_task is not None:
                agent_llm._current_task = self.current_task

            try:
                return await agent_llm.ainvoke(question)
            except Exception as e:
                logger.error(f"Sub-agent {self.agent_config.name} failed: {str(e)}")
                return f"Error in sub-agent {self.agent_config.name}: {str(e)} (Check connections or config)"
            finally:
                try:
                    # Generic cleanup (handles browser if assigned as builtin)
                    await agent_llm.cleanup()
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup sub-agent {self.agent_config.name}: {str(cleanup_error)}")

        # ----------------------- Input schema --------------------------- #
        description = _(
            "Question or instruction sent to the agent %(name)s"
        ) % {"name": self.agent_config.name}

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
            r"[^a-zA-Z0-9_-]+", "_", f"agent_{self.agent_config.name.lower()}"
        )[:64]

        # ------------------ Tool description --------------------------- #
        tool_description = self.agent_config.tool_description

        return StructuredTool.from_function(
            func=None,  # No sync func needed (async preferred)
            coroutine=execute_agent,  # Set as coroutine for async invocation
            name=safe_name,
            description=tool_description,
            args_schema=input_schema,
        )
