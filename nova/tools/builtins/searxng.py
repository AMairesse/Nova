# nova/tools/builtins/searxng.py
from django.utils.translation import gettext_lazy as _
from langchain_community.agent_toolkits.load_tools import load_tools
from asgiref.sync import sync_to_async

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool, ToolCredential

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
    # Manage between user and system tools
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    agent_user = await sync_to_async(lambda: agent.user, thread_sensitive=False)()
    if tool_user is not None and tool_user != agent_user:
        raise ValueError(_("This tool is not owned by the current user."))

    # Get the config values
    cred = await sync_to_async(
        ToolCredential.objects.filter(user=tool_user, tool=tool).first,
        thread_sensitive=False
    )()
    if not cred:
        raise ValueError(_("No credential configured for this SearXNG tool."))

    host = cred.config.get("searxng_url")
    if not host:
        raise ValueError(_("Field ‘searxng_url’ is missing from the configuration."))

    num_results = int(cred.config.get("num_results", 5))
    tools = await sync_to_async(load_tools, thread_sensitive=False)(["searx-search-results-json"], searx_host=host,
                                                                    num_results=num_results)

    return tools
