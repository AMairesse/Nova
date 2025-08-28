# nova/tools/builtins/searxng.py
from django.utils.translation import gettext_lazy as _
from langchain_community.agent_toolkits.load_tools import load_tools
from asgiref.sync import sync_to_async

from nova.llm.llm_agent import LLMAgent
from nova.models.models import Tool

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


async def get_functions(tool: Tool, agent: LLMAgent):
    endpoint = tool.endpoint
    if not endpoint:
        raise ValueError(_("No endpoint configured for this SearXNG tool."))

    tools = await sync_to_async(load_tools, thread_sensitive=False)(["searx-search-results-json"],
                                                                    searx_host=endpoint,
                                                                    num_results=5)

    return tools
