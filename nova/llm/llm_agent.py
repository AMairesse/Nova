# nova/llm/llm_agent.py
import uuid
import logging
from typing import Any, Callable, List
from django.conf import settings

# Load the langchain tools
from langchain_mistralai.chat_models import ChatMistralAI
from langchain_ollama.chat_models import ChatOllama
from langchain_openai.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Provider import ProviderType, LLMProvider
from nova.models.Thread import Thread
from nova.models.Tool import Tool
from nova.llm.checkpoints import get_checkpointer
from nova.llm.prompts import nova_system_prompt
from nova.llm.tool_error_handling import handle_tool_errors
from nova.llm.agent_middleware import AgentContext
from nova.llm.summarization_middleware import SummarizationMiddleware
from nova.utils import extract_final_answer
from .llm_tools import load_tools
from asgiref.sync import sync_to_async
from nova.models.Thread import Thread as ThreadModel

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
    ProviderType.LLAMA_CPP,
    lambda p: ChatOpenAI(
        model=p.model,
        openai_api_key="None",
        base_url=p.base_url,
        temperature=0,
        max_retries=2,
        streaming=True
    )
)
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


def create_provider_llm(provider: LLMProvider):
    """Create a LangChain chat model from a provider configuration."""
    factory = _provider_factories.get(provider.provider_type)
    if not factory:
        raise ValueError(f"Unsupported provider type: {provider.provider_type}")
    return factory(provider)


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
                     agent_config: AgentConfig, parent_config=None,
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

        # Keep reference for continuous checkpoint context rebuild.
        agent.checkpoint_link = checkpointLink

        # Store checkpointer for cleanup
        agent.checkpointer = checkpointer

        # Load tools async after init (extracted to llm_tools.py)
        tools = await load_tools(agent)

        llm = agent.create_llm_agent()

        # Store LLM reference for token counting
        agent.llm = llm

        # Update middleware with LLM
        for mw in agent.middleware:
            if hasattr(mw, 'summarizer') and mw.summarizer.agent_llm is None:
                mw.summarizer.agent_llm = llm

        # Create the ReAct agent with middleware
        middleware = [nova_system_prompt, handle_tool_errors]
        if checkpointer:
            agent.langchain_agent = create_agent(
                llm,
                tools=tools,
                middleware=middleware,
                context_schema=AgentContext,
                checkpointer=checkpointer
            )
        else:
            agent.langchain_agent = create_agent(
                llm,
                tools=tools,
                middleware=middleware,
                context_schema=AgentContext
            )

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
                # Store client reference for cleanup
                self._langfuse_client = langfuse
                langfuse_handler = CallbackHandler(public_key=langfuse_public_key)
                self._langfuse_handler = langfuse_handler

                if not langfuse.auth_check():
                    logger.warning(
                        "Langfuse auth check failed for user %s. Tracing will still be attempted.",
                        getattr(self.user, "id", "unknown"),
                    )
                self.config = {
                    "callbacks": [langfuse_handler],
                    "metadata": {
                        "langfuse_session_id": str(langgraph_thread_id),
                    },
                }
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
        self.checkpointer = None
        self.middleware = []  # Agent middleware list

        # Add summarization middleware if configured
        # NOTE: In continuous mode we use day summaries + explicit checkpoint rebuild,
        # so thread-level summarization/compaction middleware must be disabled.
        if (
            hasattr(self.agent_config, 'auto_summarize')
            and (not self.thread or self.thread.mode != ThreadModel.Mode.CONTINUOUS)
        ):
            self.middleware.append(SummarizationMiddleware(self.agent_config, self))

    async def cleanup(self):
        """Async cleanup method to close resources for loaded builtin modules, Langfuse client, and checkpointer."""
        # Cleanup Langfuse client
        if hasattr(self, '_langfuse_client') and self._langfuse_client:
            try:
                self._langfuse_client.flush()
                self._langfuse_client.shutdown()
            except Exception as e:
                logger.warning(f"Failed to cleanup Langfuse client: {e}")

        # Cleanup checkpointer
        if self.checkpointer:
            try:
                await self.checkpointer.conn.close()
            except Exception as e:
                logger.warning(f"Failed to cleanup checkpointer: {e}")

        # Cleanup builtin modules
        for module in self._loaded_builtin_modules:
            if hasattr(module, 'close'):
                await module.close(self)

    def create_llm_agent(self):
        if not self._llm_provider:
            raise Exception("No LLM provider configured")

        return create_provider_llm(self._llm_provider)

    def _build_runtime_config(self, *, silent_mode: bool = False, thread_id_override: str | None = None):
        """Build an invocation config without mutating shared config dictionaries."""
        base = self.silent_config if silent_mode else self.config
        runtime = base.copy()

        runtime_callbacks = list(base.get("callbacks", []))
        runtime["callbacks"] = runtime_callbacks

        configurable = dict(base.get("configurable", {}))
        if thread_id_override is not None:
            configurable["thread_id"] = str(thread_id_override)
        if configurable:
            runtime["configurable"] = configurable

        # Set the recursion limit
        if self.recursion_limit is not None:
            runtime.update({"recursion_limit": self.recursion_limit})
        return runtime

    async def ainvoke(self, question: str, silent_mode=False, thread_id_override: str | None = None):
        config = self._build_runtime_config(
            silent_mode=silent_mode,
            thread_id_override=thread_id_override,
        )

        # Create context for middleware
        # Find progress handler from callbacks if available
        progress_handler = None
        for callback in self.config.get('callbacks', []):
            if hasattr(callback, 'on_summarization_complete'):  # Check if it's our TaskProgressHandler
                progress_handler = callback
                break

        agent_context = AgentContext(
            agent_config=self.agent_config,
            user=self.user,
            thread=self.thread,
            progress_handler=progress_handler,
            tool_prompt_hints=list(getattr(self, "tool_prompt_hints", []) or []),
        )

        full_question = f"{question}"
        message = HumanMessage(content=full_question)

        while True:
            result = await self.langchain_agent.ainvoke(
                {"messages": message},
                config=config,
                context=agent_context
            )

            # Call after_message middleware
            for middleware in self.middleware:
                await middleware.after_message(agent_context, result)

            # If the result contains an interruption then stop processing and
            # return the interruption
            if '__interrupt__' in result:
                return result

            messages = result.get('messages', [])
            last_message = messages[-1]

            # If the result is the specific "read_image" tool then we need to add an image
            # message. The agent can call the tool multiple times in one turn so we need
            # to loop through the last messages
            if isinstance(last_message, ToolMessage) and last_message.name == "read_image":
                # Loop through messages in reverse order to find all "read_image" tool calls
                # since the last HumanMessage
                image_artifacts = []
                for msg in reversed(messages):
                    if isinstance(msg, HumanMessage):
                        # Found the last HumanMessage, stop looking
                        break
                    elif isinstance(msg, ToolMessage) and msg.name == "read_image":
                        # Collect all "read_image" tool artifacts
                        artifact = msg.artifact
                        if artifact and "base64" in artifact and "mime_type" in artifact:
                            image_artifacts.append(artifact)

                # If we found any image artifacts, create a multimodal message with all images
                if image_artifacts:
                    # Reverse the order of the images to match the order of the tool calls
                    image_artifacts = image_artifacts[::-1]

                    # List all images in order to help the agent because not all LLM can read the
                    # file name in the image type response
                    text_response = "Here are the images:\n"
                    text_response += "".join(
                        [artifact["filename"] + "\n" for artifact in image_artifacts]
                    )
                    content_parts = [{"type": "text", "text": text_response}]

                    # Add all images to the content
                    for artifact in image_artifacts:
                        content_parts.append({
                            "type": "image",
                            "source_type": "base64",
                            "data": artifact["base64"],
                            "mime_type": artifact["mime_type"],
                            "filename": artifact["filename"],
                        })

                    # Generate a new multimodal message with all images
                    message = HumanMessage(content=content_parts)
                else:
                    # No valid image artifacts found, continue with normal flow
                    final_msg = extract_final_answer(result)
                    return final_msg
            else:
                # Agent has finished, extract final answer
                final_msg = extract_final_answer(result)
                return final_msg

    async def aresume(self, command, silent_mode=False, thread_id_override: str | None = None):
        config = self._build_runtime_config(
            silent_mode=silent_mode,
            thread_id_override=thread_id_override,
        )

        # Create context for middleware
        # Find progress handler from callbacks if available
        progress_handler = None
        for callback in self.config.get('callbacks', []):
            if hasattr(callback, 'on_summarization_complete'):  # Check if it's our TaskProgressHandler
                progress_handler = callback
                break

        context = AgentContext(
            agent_config=self.agent_config,
            user=self.user,
            thread=self.thread,
            progress_handler=progress_handler,
            tool_prompt_hints=list(getattr(self, "tool_prompt_hints", []) or []),
        )

        while True:
            result = await self.langchain_agent.ainvoke(
                command,
                config=config,
                context=context
            )

            # If the result contains an interruption then stop processing and
            # return the interruption
            if '__interrupt__' in result:
                return result

            # Agent has finished, extract final answer
            final_msg = extract_final_answer(result)
            return final_msg

    async def get_langgraph_state(self):
        return await sync_to_async(self.langchain_agent.get_state, thread_sensitive=False)(self.config)

    async def count_tokens(self, messages):
        """Count tokens in messages using the agent's LLM."""
        if hasattr(self, 'llm') and self.llm:
            try:
                # Try async count_tokens first
                if hasattr(self.llm, 'count_tokens'):
                    return await self.llm.count_tokens(messages)
            except (AttributeError, TypeError):
                pass

            try:
                # Try sync count_tokens
                if hasattr(self.llm, 'count_tokens'):
                    return self.llm.count_tokens(messages)
            except (AttributeError, TypeError):
                pass

            # Fallback: rough estimate based on model
            total_chars = sum(len(str(msg.content)) for msg in messages)
            # Adjust estimate based on model type (GPT models are more token-efficient)
            model_name = getattr(self.llm, 'model_name', '') or getattr(self.llm, 'model', '')
            if 'gpt-4' in model_name.lower():
                return total_chars // 3  # GPT-4 is more efficient
            elif 'gpt-3.5' in model_name.lower():
                return total_chars // 4  # Standard estimate
            else:
                return total_chars // 5  # Conservative estimate for other models
        return 0
