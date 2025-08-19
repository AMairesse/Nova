from typing import List

from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from playwright.async_api import async_playwright

from nova.llm.llm_agent import LLMAgent
from nova.models.models import Tool

METADATA = {
    'name': 'Browser',
    'description': 'Interact with web pages using PlayWright (navigate, extract text, etc.)',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


async def init(agent: LLMAgent) -> None:
    """
    Initialise le navigateur async et le stocke dans agent._resources.
    Appelé avant la création du LLM pour persistance.
    """
    if 'browser' in agent._resources:
        return

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    agent._resources['playwright_async'] = playwright
    agent._resources['browser'] = browser


async def close(agent: LLMAgent) -> None:
    """
    Ferme le navigateur et nettoie agent._resources.
    Appelé en fin d'usage de l'agent.
    """
    browser = agent._resources.get('browser')
    playwright = agent._resources.get('playwright_async')
    if browser:
        try:
            if browser.is_connected():
                await browser.close()
        finally:
            del agent._resources['browser']
    if playwright:
        try:
            await playwright.stop()
        finally:
            del agent._resources['playwright_async']


async def get_functions(tool: Tool, agent: LLMAgent) -> List:
    """
    Retourne les tools PlayWright (list of BaseTool), utilisant le navigateur persistant de agent._resources.
    Ignores 'tool' as this builtin doesn't need config/user-specific data.
    """
    _ = tool  # Ignore 'tool'

    browser = agent._resources.get('browser')
    if not browser:
        raise ValueError("Browser not initialized. Ensure init() was called.")

    toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
    return toolkit.get_tools()
