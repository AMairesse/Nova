# nova/llm_agent.py
from datetime import date
import re
import logging
import inspect
from typing import Any, Callable, List, Optional, Dict
from functools import wraps

# Load the langchain tools
from langchain_mistralai.chat_models import ChatMistralAI
from langchain_ollama.chat_models import ChatOllama
from langchain_openai.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import StructuredTool
from langchain_core.callbacks import BaseCallbackHandler
from .models import Actor, Tool, ProviderType, LLMProvider
from .utils import extract_final_answer

import asyncio
import nest_asyncio
nest_asyncio.apply()  # Enable nested loops for mixed sync/async compatibility
from playwright.async_api import async_playwright
from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from asgiref.sync import sync_to_async  # For async-safe ORM in factory

logger = logging.getLogger(__name__)


# Factory dictionary for LLM creation
# --------------------------------------------------------------------- #
_provider_factories = {}  # type: Dict[ProviderType,
#                                      Callable[[LLMProvider], Any]]


def register_provider(type_: ProviderType,
                      factory: Callable[[LLMProvider], Any]) -> None:
    """Register a factory function for a provider type."""
    _provider_factories[type_] = factory


# Register built-in providers
# (call this once at app startup, or in settings.py)
register_provider(
    ProviderType.MISTRAL,
    lambda p: ChatMistralAI(
        model=p.model,
        mistral_api_key=p.api_key,
        temperature=0,
        max_retries=2,
        streaming=True
    )
)
register_provider(
    ProviderType.OPENAI,
    lambda p: ChatOpenAI(
        model=p.model,
        openai_api_key=p.api_key,
        base_url=p.base_url,
        temperature=0,
        max_retries=2,
        streaming=True
    )
)
register_provider(
    ProviderType.OLLAMA,
    lambda p: ChatOllama(
        model=p.model,
        base_url=p.base_url or "http://localhost:11434",
        temperature=0,
        max_retries=2,
        streaming=True
    )
)
register_provider(
    ProviderType.LLMSTUDIO,
    lambda p: ChatOpenAI(
        model=p.model,
        openai_api_key="None",
        base_url=p.base_url or "http://localhost:1234/v1",
        temperature=0,
        max_retries=2,
        streaming=True
    )
)


# Example: Adding a new provider (can be done anywhere, even in a plugin)
# register_provider(ProviderType.ANTHROPIC, lambda p: ChatAnthropic(...))
# --------------------------------------------------------------------- #
class LLMAgent:
    @staticmethod
    async def create_browser(headless=True):
        """Static method to create and return an async browser and playwright instance."""
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=headless)
        return browser, playwright

    @classmethod
    async def create(cls, user, thread_id, msg_history=None, agent=None,
                     parent_config=None,
                     callbacks: List[BaseCallbackHandler] = None,
                     async_browser: Optional[Any] = None,
                     playwright_async: Optional[Any] = None):
        """
        Async factory to create an LLMAgent instance with async-safe ORM accesses.
        Wraps sync field/related model fetches.
        """
        # Sync function to fetch user parameters safely
        def fetch_user_params_sync(user):
            try:
                user_params = user.userparameters
                allow_langfuse = user_params.allow_langfuse
                langfuse_public_key = user_params.langfuse_public_key
                langfuse_secret_key = user_params.langfuse_secret_key
                langfuse_host = user_params.langfuse_host or None
            except AttributeError:
                allow_langfuse = False
                langfuse_public_key = None
                langfuse_secret_key = None
                langfuse_host = None
            return allow_langfuse, langfuse_public_key, langfuse_secret_key, langfuse_host

        allow_langfuse, langfuse_public_key, langfuse_secret_key, langfuse_host = await sync_to_async(fetch_user_params_sync)(user)

        # Pre-fetch ORM data for _load_agent_tools
        def fetch_agent_data_sync(agent, user):
            if not agent:
                return [], [], [], False, None, None
            builtin_tools = list(agent.tools.filter(is_active=True, tool_type=Tool.ToolType.BUILTIN))
            mcp_tools_data = []
            mcp_tools = list(agent.tools.filter(tool_type=Tool.ToolType.MCP, is_active=True))
            for tool in mcp_tools:
                cred = tool.credentials.filter(user=user).first()
                cred_user_id = cred.user.id if cred and cred.user else None
                if tool.available_functions:
                    func_metas = list(tool.available_functions.values())
                else:
                    func_metas = None
                mcp_tools_data.append((tool, cred, func_metas, cred_user_id))
            agent_tools = list(agent.agent_tools.filter(is_tool=True))
            has_agent_tools = agent.agent_tools.exists()
            system_prompt = agent.system_prompt
            llm_provider = agent.llm_provider
            return builtin_tools, mcp_tools_data, agent_tools, has_agent_tools, system_prompt, llm_provider

        builtin_tools, mcp_tools_data, agent_tools, has_agent_tools, system_prompt, llm_provider = await sync_to_async(fetch_agent_data_sync)(agent, user)

        instance = cls(
            user=user,
            thread_id=thread_id,
            msg_history=msg_history,
            agent=agent,
            parent_config=parent_config,
            callbacks=callbacks,
            async_browser=async_browser,
            playwright_async=playwright_async,
            allow_langfuse=allow_langfuse,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            langfuse_host=langfuse_host,
            builtin_tools=builtin_tools,  # Pass pre-fetched
            mcp_tools_data=mcp_tools_data,
            agent_tools=agent_tools,
            has_agent_tools=has_agent_tools,
            system_prompt=system_prompt,
            llm_provider=llm_provider
        )
        return instance

    def __init__(self, user, thread_id, msg_history=None, agent=None,
                 parent_config=None,
                 callbacks: List[BaseCallbackHandler] = None,
                 async_browser: Optional[Any] = None,
                 playwright_async: Optional[Any] = None,
                 allow_langfuse=False,
                 langfuse_public_key=None,
                 langfuse_secret_key=None,
                 langfuse_host=None,
                 builtin_tools=None,  # Pre-fetched params
                 mcp_tools_data=None,
                 agent_tools=None,
                 has_agent_tools=False,
                 system_prompt=None,
                 llm_provider=None):
        if msg_history is None:
            msg_history = []
        if callbacks is None:
            callbacks = []  # Default to empty list for custom callbacks
        self.user = user
        self.django_agent = agent
        self.thread_id = thread_id

        # Inherit from parent config
        if parent_config and 'callbacks' in parent_config:
            self.config = parent_config.copy()
            # Only update thread_id
            self.config.update({"configurable": {"thread_id": thread_id}})
        else:
            # Build a new config if needed
            self.config = {}
            if allow_langfuse and langfuse_public_key and langfuse_secret_key:
                try:
                    from langfuse import Langfuse
                    from langfuse.langchain import CallbackHandler

                    # Create/Configure Langfuse client (once at startup)
                    langfuse = Langfuse(
                        public_key=langfuse_public_key,
                        secret_key=langfuse_secret_key,
                        host=langfuse_host,
                    )
                    langfuse_handler = CallbackHandler()

                    langfuse.auth_check()
                    self.config = {"callbacks": [langfuse_handler]}
                except Exception as e:
                    logger.error(f"Failed to create Langfuse client: {e}", exc_info=e)  # Log error but continue without Langfuse
                    self.config = {}
            self.config.update({"configurable": {"thread_id": thread_id}})

        # Merge custom callbacks with existing ones (e.g., Langfuse)
        # Create a copy of the config without
        # custom callbacks for "silent_mode"
        # Warning : this is not a deep copy because we need to keep
        # the same callbacks but we also need to do an explicit copy
        # of the callbacks' list so that the copy is not updated in sync
        # with the original
        self.silent_config = self.config.copy()
        self.silent_config['callbacks'] = list(self.config['callbacks'])
        if 'callbacks' in self.config:
            self.config['callbacks'].extend(callbacks)
        else:
            self.config['callbacks'] = callbacks

        # Store the parent config in order to be
        # able to propagate it to child agents
        self._parent_config = self.config.copy()

        # Use provided browsers if available
        self.async_browser = async_browser
        self.playwright_async = playwright_async

        # Pre-fetched for tool loading and prompt/llm
        self.builtin_tools = builtin_tools or []
        self.mcp_tools_data = mcp_tools_data or []
        self.agent_tools = agent_tools or []
        self.has_agent_tools = has_agent_tools
        self._system_prompt = system_prompt
        self._llm_provider = llm_provider

        # Get agent's tools (browsers must be injected)
        tools = self._load_agent_tools()

        memory = MemorySaver()

        llm = self.create_llm_agent()
        system_prompt = self.build_system_prompt()

        # Create the agent
        self.agent = create_react_agent(llm, tools=tools,
                                        prompt=system_prompt,
                                        checkpointer=memory)

        # Load previous exchanges
        for actor, message in msg_history:
            if actor == Actor.USER:
                self.agent.update_state(
                    self.config,
                    {"messages": [HumanMessage(content=message)]}
                )
            else:
                self.agent.update_state(
                    self.config,
                    {"messages": [AIMessage(content=message)]}
                )

    def _load_agent_tools(self):
        """
        Load and initialize tools associated with the agent.
        Returns a list of Langchain-ready tools.
        """
        tools = []

        # Load builtin tools (pre-fetched)
        for tool_obj in self.builtin_tools:
            tools.extend(self._create_tool_functions(tool_obj))

        # Load MCP tools (pre-fetched data: (tool, cred, func_metas, cred_user_id))
        for tool_obj, cred, cached_func_metas, cred_user_id in self.mcp_tools_data:
            try:
                from nova.mcp.client import MCPClient
                client = MCPClient(
                    endpoint=tool_obj.endpoint, 
                    thread_id=self.thread_id,
                    credential=cred, 
                    transport_type=tool_obj.transport_type,
                    user_id=cred_user_id
                )

                # Use pre-fetched or fetch via client
                if cached_func_metas is not None:
                    func_metas = cached_func_metas
                else:
                    func_metas = client.list_tools(force_refresh=True)

                for meta in func_metas:
                    func_name = meta["name"]
                    input_schema = meta.get("input_schema", {})
                    description = meta.get("description", "")

                    # ---------- safe factory captures current func_name & client -----------
                    def _remote_call_factory(_name: str, _client: MCPClient):
                        async def _remote_call_async(**kwargs):
                            return await _client.acall(_name, **kwargs)

                        def _remote_call_sync(**kwargs):
                            return _client.call(_name, **kwargs)

                        return _remote_call_sync, _remote_call_async
                    # -----------------------------------------------------------------------

                    sync_f, async_f = _remote_call_factory(func_name, client)

                    wrapped = StructuredTool.from_function(
                        func=sync_f,
                        coroutine=async_f,
                        name=re.sub(r"[^a-zA-Z0-9_-]+", "_", func_name)[:64],
                        description=description,
                        args_schema=None if input_schema == {} else input_schema,
                    )
                    tools.append(wrapped)

            except Exception as e:
                logger.warning(f"Failed to load MCP tools from {tool_obj.endpoint}: {str(e)}")

        # Load agents used as tools (pre-fetched)
        if self.has_agent_tools:
            from nova.tools.agent_tool_wrapper import AgentToolWrapper

            for agent_tool in self.agent_tools:
                wrapper = AgentToolWrapper(
                    agent_tool, 
                    self.user,
                    parent_config=self._parent_config
                )
                langchain_tool = wrapper.create_langchain_tool()
                tools.append(langchain_tool)

        # Load browser tools (assumes injected async_browser)
        toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=self.async_browser)
        browser_tools = toolkit.get_tools()
        tools.extend(browser_tools)

        return tools

    async def close_browsers(self):
        """Async cleanup method to close browsers and stop Playwright instances when done."""
        if self.async_browser and self.async_browser.is_connected():
            await self.async_browser.close()
        if self.playwright_async:
            await self.playwright_async.stop()

    def _create_tool_functions(self, tool_obj):
        """
        Create Langchain tool functions from the tool object.
        """
        functions = []

        try:
            from nova.tools import import_module, get_metadata
            module = import_module(tool_obj.python_path)

            # Check if the module has a get_functions() method
            if not module or not hasattr(module, 'get_functions'):
                return functions

            function_configs = module.get_functions()

            for func_name, func_config in function_configs.items():
                func = func_config["callable"]

                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                needs_inj = len(params) >= 2 and params[0] == "user"\
                    and params[1] == "tool_id"

                if needs_inj:
                    # ---------- safe wrapper captures current func & tool_id ------------
                    def _inject_user_tool(f=func, _tool_id=tool_obj.id,
                                          _user=self.user):
                        @wraps(f)
                        def wrapper(*args, **kwargs):
                            return f(_user, _tool_id, *args, **kwargs)
                        return wrapper
                    wrapped_func = _inject_user_tool()
                    # -------------------------------------------------------------------
                else:
                    wrapped_func = func

                safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", func_name)[:64]

                langchain_tool = StructuredTool.from_function(
                    func=wrapped_func,
                    coroutine=func_config.get("async_callable"),  # Use .get() to make optional (None if missing)
                    name=safe_name,
                    description=func_config["description"],
                    args_schema=func_config["input_schema"],
                )
                functions.append(langchain_tool)

        except Exception as e:
            logger.error(f"Error creating functions for tool {tool_obj.name}: {str(e)}")

        return functions

    def build_system_prompt(self):
        """
        Build the system prompt.
        """
        today = date.today().strftime("%A %d of %B, %Y")

        if self._system_prompt:
            sp = self._system_prompt
            if "{today}" in sp:
                sp = sp.format(today=today)
            return sp

        return (
            f"You are a helpful assistant. Today is {today}. "
            "Be concise and direct. If you need to display "
            "structured information, use markdown."
        )
    
    def create_llm_agent(self):
        if not self.django_agent or not self._llm_provider:
            raise Exception("No LLM provider configured")
            
        provider = self._llm_provider
        
        factory = _provider_factories.get(provider.provider_type)
        if not factory:
            raise ValueError(f"Unsupported provider type: {provider.provider_type}")
        return factory(provider)

    async def invoke(self, question: str, silent_mode=False):  # Now async
        config = self.silent_config if silent_mode else self.config
        result = await self.agent.ainvoke(  # Switch to ainvoke and await it
            {"messages": [HumanMessage(content=question)]},
            config=config
        )
        final_msg = extract_final_answer(result)
        return final_msg
