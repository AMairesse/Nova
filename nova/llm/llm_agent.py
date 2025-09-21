# nova/llm/llm_agent.py
from datetime import date
import uuid
import logging
from typing import Any, Callable, List
from django.conf import settings

# Load the langchain tools
from langchain_mistralai.chat_models import ChatMistralAI
from langchain_ollama.chat_models import ChatOllama
from langchain_openai.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from langchain_core.callbacks import BaseCallbackHandler
from nova.models.models import Agent, Tool, ProviderType, LLMProvider, UserInfo
from nova.models.models import CheckpointLink, UserFile
from nova.models.Thread import Thread
from nova.llm.checkpoints import get_checkpointer
from nova.utils import extract_final_answer
from .llm_tools import load_tools
from asgiref.sync import sync_to_async

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
        reasoning=False,
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
    @classmethod
    def fetch_user_params_sync(cls, user):
        # Sync function to fetch user parameters safely
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
        return allow_langfuse, langfuse_public_key, \
            langfuse_secret_key, langfuse_host

    @classmethod
    def fetch_agent_data_sync(cls, agent_config, user):
        # Pre-fetch ORM data for load_tools
        if not agent_config:
            return [], [], [], False, None, None
        builtin_tools = list(agent_config.tools.filter(is_active=True,
                                                       tool_type=Tool.ToolType.BUILTIN))
        mcp_tools_data = []
        mcp_tools = list(agent_config.tools.filter(tool_type=Tool.ToolType.MCP,
                                                   is_active=True))
        for tool in mcp_tools:
            cred = tool.credentials.filter(user=user).first()
            cred_user_id = cred.user.id if cred and cred.user else None
            if tool.available_functions:
                func_metas = list(tool.available_functions.values())
            else:
                func_metas = None
            mcp_tools_data.append((tool, cred, func_metas, cred_user_id))
        agent_tools = list(agent_config.agent_tools.filter(is_tool=True))
        has_agent_tools = agent_config.agent_tools.exists()
        system_prompt = agent_config.system_prompt
        recursion_limit = agent_config.recursion_limit
        llm_provider = agent_config.llm_provider
        return builtin_tools, mcp_tools_data, agent_tools, \
            has_agent_tools, system_prompt, recursion_limit, \
            llm_provider

    @classmethod
    async def create(cls, user: settings.AUTH_USER_MODEL, thread: Thread,
                     agent_config: Agent, parent_config=None,
                     callbacks: List[BaseCallbackHandler] = None):
        """
        Async factory to create an LLMAgent instance (an agent) with
        async-safe ORM accesses.
        Wraps sync field/related model fetches.
        """
        allow_langfuse,  langfuse_public_key, langfuse_secret_key, \
            langfuse_host = await sync_to_async(cls.fetch_user_params_sync,
                                                thread_sensitive=False)(user)

        builtin_tools, mcp_tools_data, agent_tools, has_agent_tools, \
            system_prompt, recursion_limit, \
            llm_provider = await sync_to_async(cls.fetch_agent_data_sync,
                                               thread_sensitive=False)(agent_config, user)

        # If there is a thread into the call then link a checkpoint to it
        if thread:
            # Get or create the CheckpointLink
            checkpointLink, _ = await sync_to_async(CheckpointLink.objects.get_or_create, thread_sensitive=False)(
                thread=thread,
                agent=agent_config
            )
            checkpointer = await get_checkpointer()
            langgraph_thread_id = checkpointLink.checkpoint_id
        else:
            checkpointLink = None
            checkpointer = None
            langgraph_thread_id = uuid.uuid4()

        agent = cls(
            user=user,
            thread=thread,
            langgraph_thread_id=langgraph_thread_id,
            agent_config=agent_config,
            callbacks=callbacks,
            allow_langfuse=allow_langfuse,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            langfuse_host=langfuse_host,
            builtin_tools=builtin_tools,
            mcp_tools_data=mcp_tools_data,
            agent_tools=agent_tools,
            has_agent_tools=has_agent_tools,
            system_prompt=system_prompt,
            recursion_limit=recursion_limit,
            llm_provider=llm_provider
        )

        # Load tools async after init (extracted to llm_tools.py)
        tools = await load_tools(agent)

        llm = agent.create_llm_agent()
        system_prompt = await agent.build_system_prompt()

        # Create the ReAct agent
        if checkpointer:
            agent.langchain_agent = create_react_agent(llm, tools=tools,
                                                       prompt=system_prompt,
                                                       checkpointer=checkpointer)
        else:
            agent.langchain_agent = create_react_agent(llm, tools=tools,
                                                       prompt=system_prompt)

        agent.tools = tools

        return agent

    def __init__(self, user: settings.AUTH_USER_MODEL,
                 thread: Thread,
                 langgraph_thread_id,
                 agent_config=None,
                 callbacks: List[BaseCallbackHandler] = None,
                 allow_langfuse=False,
                 langfuse_public_key=None,
                 langfuse_secret_key=None,
                 langfuse_host=None,
                 builtin_tools=None,  # Pre-fetched params
                 mcp_tools_data=None,
                 agent_tools=None,
                 has_agent_tools=False,
                 system_prompt=None,
                 recursion_limit=None,
                 llm_provider=None):
        if callbacks is None:
            callbacks = []  # Default to empty list for custom callbacks
        self.user = user
        self.thread = thread
        self.agent_config = agent_config

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

                if langfuse.auth_check():
                    self.config = {"callbacks": [langfuse_handler],
                                   "metadata": {
                                     "langfuse_session_id": str(langgraph_thread_id),
                                  },
                                  }
                else:
                    self.config = {}
            except Exception as e:
                logger.error(f"Failed to create Langfuse client: {e}",
                             exc_info=e)  # Log error but continue without
                self.config = {}
        self.config.update({"configurable": {"thread_id": str(langgraph_thread_id)}})

        # Ensure the 'callbacks' key exists and keep copies decoupled
        existing_callbacks = list(self.config.get('callbacks', []))

        # Copy config for silent mode, but with its own callbacks list
        # Warning : this is not a deep copy because we need to keep
        # the same callbacks but we also need to do an explicit copy
        # of the callbacks' list so that the copy is not updated in sync
        # with the original
        self.silent_config = self.config.copy()
        self.silent_config['callbacks'] = list(existing_callbacks)

        # Merge custom callbacks into the main config
        self.config['callbacks'] = existing_callbacks + (callbacks or [])

        # Store the parent config in order to be
        # able to propagate it to child agents
        self._parent_config = self.config.copy()

        # Pre-fetched for tool loading and prompt/llm
        self.builtin_tools = builtin_tools or []
        self.mcp_tools_data = mcp_tools_data or []
        self.agent_tools = agent_tools or []
        self.has_agent_tools = has_agent_tools
        self._system_prompt = system_prompt
        self.recursion_limit = recursion_limit
        self._llm_provider = llm_provider

        # Initialize resources and loaded modules tracker
        self._resources = {}
        self._loaded_builtin_modules = []

    async def cleanup(self):
        """Async cleanup method to close resources for loaded builtin modules."""
        for module in self._loaded_builtin_modules:
            if hasattr(module, 'close'):
                await module.close(self)

    async def build_system_prompt(self):
        """
        Build the system prompt.
        """
        today = date.today().strftime("%A %d of %B, %Y")

        base_prompt = ""
        if self._system_prompt:
            base_prompt = self._system_prompt
            if "{today}" in base_prompt:
                base_prompt = base_prompt.format(today=today)
        else:
            base_prompt = (
                f"You are a helpful assistant. Today is {today}. "
                "Be concise and direct. If you need to display "
                "structured information, use markdown."
            )

        # Check if memory tool is enabled and inject user memory
        memory_tool_enabled = any(
            tool.tool_subtype == 'memory' and tool.is_active
            for tool in self.builtin_tools
        )

        if memory_tool_enabled:
            try:
                user_info = await sync_to_async(UserInfo.objects.get)(user=self.user)
                themes = await sync_to_async(user_info.get_themes)()
                if themes:
                    memory_block = f"\n\nAvailable themes in memory, use tools to read them: {', '.join(themes)}"
                    base_prompt += memory_block
            except UserInfo.DoesNotExist:
                # UserInfo should exist due to signal, but handle gracefully
                pass
            except Exception as e:
                logger.warning(f"Failed to load user memory: {e}")

        # Add information about files available in disussion
        list_files = await sync_to_async(UserFile.objects.filter,
                                         thread_sensitive=False)(thread=self.thread)
        num_files = await sync_to_async(list_files.count,
                                        thread_sensitive=False)()
        if num_files > 0:
            files_context = f"\nThere is {num_files} attached files. Use file tools if needed."
        else:
            files_context = "\nNo attached files available."
        base_prompt += files_context

        return base_prompt

    def create_llm_agent(self):
        if not self._llm_provider:
            raise Exception("No LLM provider configured")

        provider = self._llm_provider

        factory = _provider_factories.get(provider.provider_type)
        if not factory:
            raise ValueError(f"Unsupported provider type: {provider.provider_type}")
        return factory(provider)

    async def ainvoke(self, question: str, silent_mode=False):
        config = self.silent_config if silent_mode else self.config

        # Set the recursion limit
        if self.recursion_limit is not None:
            config.update({"recursion_limit": self.recursion_limit})

        full_question = f"{question}"

        result = await self.langchain_agent.ainvoke(
            {"messages": [HumanMessage(content=full_question)]},
            config=config
        )

        messages = result.get('messages', [])
        last_message = messages[-1]
        # If the result contains an artefact, call again the agent with it
        if isinstance(last_message, ToolMessage) and last_message.name == "read_image" and hasattr(last_message, 'artifact') and last_message.artifact:
            artifact = last_message.artifact

            base64_image = artifact["base64"]
            mime_type = artifact["mime_type"]

            # Créez un message "humain" fictif avec l'image (format compatible avec modèles multimodaux)
            image_message = HumanMessage(
                content=[
                    {"type": "text", "text": "Here is the image."},
                    {
                        "type": "image",
                        "source_type": "base64",
                        "data": base64_image,
                        "mime_type": mime_type,
                    }
                ]
            )

            result = await self.langchain_agent.ainvoke(
                {"messages": [image_message]},
                config=config
            )

        final_msg = extract_final_answer(result)
        return final_msg
