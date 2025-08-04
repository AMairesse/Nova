# nova/llm_agent.py
from datetime import date
import re
import inspect
from typing import Any, Callable, List
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
    def __init__(self, user, thread_id, msg_history=None, agent=None,
                 parent_config=None,
                 callbacks: List[BaseCallbackHandler] = None):
        if msg_history is None:
            msg_history = []
        if callbacks is None:
            callbacks = []  # Default to empty list for custom callbacks
        self.user = user
        self.django_agent = agent

        # Get user parameters
        try:
            user_params = user.userparameters
            allow_langfuse = user_params.allow_langfuse
            langfuse_public_key = user_params.langfuse_public_key
            langfuse_secret_key = user_params.langfuse_secret_key
            # Fallback to None if not set
            langfuse_host = user_params.langfuse_host or None
        except AttributeError:
            allow_langfuse = False
            langfuse_public_key = None
            langfuse_secret_key = None
            langfuse_host = None

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
                    print(e)  # Log error but continue without Langfuse
                    self.config = {}
            self.config.update({"configurable": {"thread_id": thread_id}})

        # Merge custom callbacks with existing ones (e.g., Langfuse)
        # Create a copy of the config without
        # custom callbacks for "silent_mode"
        self.silent_config = self.config.copy()
        if 'callbacks' in self.config:
            self.config['callbacks'].extend(callbacks)
        else:
            self.config['callbacks'] = callbacks

        # Store the parent config in order to be
        # able to propagate it to child agents
        self._parent_config = self.config.copy()

        # Get agent's tools
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

        if not self.django_agent or (
            not self.django_agent.tools.exists()
            and not self.django_agent.agent_tools.exists()
        ):
            return tools

        # Load builtin tools
        for tool_obj in self.django_agent.tools.filter(is_active=True,
                                                       tool_type=Tool.ToolType.BUILTIN):
            tools.extend(self._create_tool_functions(tool_obj))

        # Load MCP tools
        for tool_obj in self.django_agent.tools.filter(tool_type=Tool.ToolType.MCP,
                                                       is_active=True):
            cred = tool_obj.credentials.filter(user=self.user).first()
            try:
                from nova.mcp.client import MCPClient
                client = MCPClient(tool_obj.endpoint, cred, tool_obj.transport_type)

                # Prefer the cached snapshot
                if tool_obj.available_functions:
                    func_metas = tool_obj.available_functions.values()
                else:
                    func_metas = client.list_tools(user_id=self.user.id)

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
                # Log and skip unreachable MCP
                logger = __import__('logging').getLogger(__name__)
                logger.warning(f"Failed to load MCP tools from {tool_obj.endpoint}: {str(e)}")

        # Load agents used as tools
        if self.django_agent.agent_tools.exists():
            from nova.tools.agent_tool_wrapper import AgentToolWrapper

            for agent_tool in self.django_agent.agent_tools.filter(is_tool=True):
                wrapper = AgentToolWrapper(
                    agent_tool, 
                    self.user,
                    parent_config=self._parent_config
                )
                langchain_tool = wrapper.create_langchain_tool()
                tools.append(langchain_tool)

        return tools

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
                    name=safe_name,
                    description=func_config["description"],
                    args_schema=func_config["input_schema"],
                )
                functions.append(langchain_tool)

        except Exception as e:
            print(f"Error creating functions for tool {tool_obj.name}: {str(e)}")
            import traceback
            traceback.print_exc()

        return functions

    def build_system_prompt(self):
        """
        Build the system prompt.
        """
        today = date.today().strftime("%A %d of %B, %Y")

        if self.django_agent and self.django_agent.system_prompt:
            sp = self.django_agent.system_prompt
            if "{today}" in sp:
                sp = sp.format(today=today)
            return sp

        return (
            f"You are a helpful assistant. Today is {today}. "
            "Be concise and direct. If you need to display "
            "structured information, use markdown."
        )
    
    def create_llm_agent(self):
        if not self.django_agent or not self.django_agent.llm_provider:
            raise Exception("No LLM provider configured")
            
        provider = self.django_agent.llm_provider
        
        factory = _provider_factories.get(provider.provider_type)
        if not factory:
            raise ValueError(f"Unsupported provider type: {provider.provider_type}")
        return factory(provider)

    def invoke(self, question: str, silent_mode=False):
        if silent_mode:
            result = self.agent.invoke({"messages":[HumanMessage(content=question)]},
                                       config=self.silent_config)
        else:
            result = self.agent.invoke({"messages":[HumanMessage(content=question)]},
                                       config=self.config)
        final_msg = extract_final_answer(result)
        return final_msg
