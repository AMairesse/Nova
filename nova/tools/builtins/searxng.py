# nova/tools/builtins/searxng.py
import json

from django.utils.translation import gettext_lazy as _
from asgiref.sync import sync_to_async
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool
from nova.web.search_service import get_searxng_config, search_web

METADATA = {
    'name': 'SearXNG',
    'description': 'Interact with a SearXNG server (search)',
    'requires_config': True,
    'config_fields': [
        {'name': 'searxng_url', 'type': 'string', 'label': _('URL SearXNG server'), 'required': True},
        {"name": 'num_results', 'type': 'integer', 'label': _('Max results'), 'required': False},
    ],
    'test_function': None,
    'test_function_args': [],
}


class _SearxSearchInput(BaseModel):
    query: str = Field(..., description="search query")


async def get_functions(tool: Tool, agent: LLMAgent):
    # Manage between user and system tools
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    agent_user = await sync_to_async(lambda: agent.user, thread_sensitive=False)()
    if tool_user is not None and tool_user != agent_user:
        raise ValueError(_("This tool is not owned by the current user."))
    await get_searxng_config(tool)

    async def _search_wrapper(query: str) -> str:
        payload = await search_web(tool, query=query)
        return json.dumps(payload["results"], ensure_ascii=False)

    return [
        StructuredTool.from_function(
            coroutine=_search_wrapper,
            name="searx_search_results",
            description=(
                "A meta search engine.Useful for when you need to answer questions "
                "about current events.Input should be a search query. "
                "Output is a JSON array of the query results"
            ),
            args_schema=_SearxSearchInput,
        )
    ]
