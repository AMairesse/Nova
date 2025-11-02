# nova/tests/test_llm_agent_mixins.py
"""
Test mixins and utilities for LLM agent testing.
Provides reusable mocking and setup patterns to reduce duplication.
"""
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nova.models.Provider import ProviderType


class LLMAgentTestMixin:
    """
    Mixin providing common LLM agent test setup and utilities.

    This mixin centralizes the complex mocking setup used across LLM agent tests,
    making individual test methods more focused and readable.
    """

    def setUpLLMAgent(self):
        """Standard LLM agent test setup with mocked third-party dependencies."""
        self.fakes = self._get_fake_third_party_modules()
        self.patch_context = patch.dict(sys.modules, self.fakes)
        self.patch_context.start()

    def tearDownLLMAgent(self):
        """Clean up mocked modules after tests."""
        if hasattr(self, 'patch_context'):
            self.patch_context.stop()

        mocked_modules = [
            "langchain_mistralai", "langchain_mistralai.chat_models",
            "langchain_ollama", "langchain_ollama.chat_models",
            "langchain_openai", "langchain_openai.chat_models",
            "langchain_core", "langchain_core.messages",
            "langgraph", "langgraph.checkpoint", "langgraph.checkpoint.memory",
            "langgraph.prebuilt",
            "nova.tools.agent_tool_wrapper",
            "nova.tools", "nova.mcp.client", "nova.tools.files",
            "nova.llm.checkpoints",
            "nova.models.Provider", "nova.models.Thread",
            "asgiref.sync",
        ]
        for mod in mocked_modules:
            sys.modules.pop(mod, None)

    def create_mock_langchain_agent(self, return_value=None):
        """Factory for creating consistent mock LangChain agents."""
        class FakeLangchainAgent:
            def __init__(self):
                self.invocations = []

            async def ainvoke(self, payload, config=None):
                self.invocations.append((payload, config))
                return return_value or {"messages": [{"content": "final answer"}]}

        return FakeLangchainAgent()

    def create_mock_provider(self, provider_type=ProviderType.OPENAI, **kwargs):
        """Factory for creating mock LLM providers."""
        defaults = {
            "provider_type": provider_type,
            "model": "gpt-4o-mini",
            "api_key": "fake_key",
            "base_url": None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def create_mock_agent_config(self, **kwargs):
        """Factory for creating mock agent configurations."""
        defaults = {
            "name": "Test Agent",
            "system_prompt": "You are a helpful assistant.",
            "recursion_limit": 25,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def create_mock_user(self, user_id=1):
        """Factory for creating mock users."""
        return SimpleNamespace(id=user_id)

    def create_mock_thread(self, thread_id=1):
        """Factory for creating mock threads."""
        return SimpleNamespace(id=thread_id)

    def _get_fake_third_party_modules(self):
        """Get the comprehensive fake module dictionary for testing."""
        # langchain_* chat models
        lc_mistral = types.ModuleType("langchain_mistralai")
        lc_mistral_chat = types.ModuleType("langchain_mistralai.chat_models")

        class ChatMistralAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_mistral_chat.ChatMistralAI = ChatMistralAI

        lc_ollama = types.ModuleType("langchain_ollama")
        lc_ollama_chat = types.ModuleType("langchain_ollama.chat_models")

        class ChatOllama:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_ollama_chat.ChatOllama = ChatOllama

        lc_openai = types.ModuleType("langchain_openai")
        lc_openai_chat = types.ModuleType("langchain_openai.chat_models")

        class ChatOpenAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_openai_chat.ChatOpenAI = ChatOpenAI

        # langchain_core.messages
        lc_core = types.ModuleType("langchain_core")
        lc_core_msgs = types.ModuleType("langchain_core.messages")

        class HumanMessage:
            def __init__(self, content):
                self.content = content

        lc_core_msgs.HumanMessage = HumanMessage

        # langgraph
        lg_root = types.ModuleType("langgraph")
        lg_mem = types.ModuleType("langgraph.checkpoint.memory")

        class MemorySaver:
            pass

        lg_mem.MemorySaver = MemorySaver

        lg_checkpoint = types.ModuleType("langgraph.checkpoint")

        lg_pre = types.ModuleType("langgraph.prebuilt")

        def create_agent(llm, tools=None, system_prompt=None, checkpointer=None):
            class DummyAgent:
                def __init__(self):
                    self.state = []

                def update_state(self, config, payload):
                    self.state.append((config, payload))

                async def ainvoke(self, payload, config=None):
                    return payload
            return DummyAgent()

        lg_pre.create_agent = create_agent

        # Fake nova.tools.agent_tool_wrapper
        atw_mod = types.ModuleType("nova.tools.agent_tool_wrapper")

        class AgentToolWrapper:
            def __init__(self, agent_config, thread, user):
                self.agent_config = agent_config
                self.thread = thread
                self.user = user

            def create_langchain_tool(self):
                return {"wrapped_agent_tool": getattr(self.agent_config, "name", "unknown")}

        atw_mod.AgentToolWrapper = AgentToolWrapper

        # Fake checkpoints
        checkpoints_mod = types.ModuleType("nova.llm.checkpoints")

        async def get_checkpointer():
            return MagicMock()

        checkpoints_mod.get_checkpointer = get_checkpointer

        # Fake models
        checkpointlink_mod = types.ModuleType("nova.models.CheckpointLink")
        userfile_mod = types.ModuleType("nova.models.UserFile")
        thread_mod = types.ModuleType("nova.models.Thread")

        # Mock CheckpointLink with proper Django manager pattern
        mock_checkpoint_link = SimpleNamespace(checkpoint_id="fake_id", id=1, thread_id=1, agent_id=1)

        class MockQuerySet:
            def get_or_create(self, **kwargs):
                return (mock_checkpoint_link, True)

        class MockManager:
            def get_queryset(self):
                return MockQuerySet()

        class CheckpointLink:
            objects = MockManager()

        checkpointlink_mod.CheckpointLink = CheckpointLink
        userfile_mod.UserFile = MagicMock()
        thread_mod.Thread = MagicMock()

        # Fake asgiref.sync
        asgiref_sync = types.ModuleType("asgiref.sync")

        def sync_to_async(func, **kwargs):
            async def wrapper(*args, **kw):
                return func(*args, **kw)
            return wrapper

        asgiref_sync.sync_to_async = sync_to_async

        # Return a dict for patch.dict
        return {
            "langchain_mistralai": lc_mistral,
            "langchain_mistralai.chat_models": lc_mistral_chat,
            "langchain_ollama": lc_ollama,
            "langchain_ollama.chat_models": lc_ollama_chat,
            "langchain_openai": lc_openai,
            "langchain_openai.chat_models": lc_openai_chat,
            "langchain_core": lc_core,
            "langchain_core.messages": lc_core_msgs,
            "langgraph": lg_root,
            "langgraph.checkpoint": lg_checkpoint,
            "langgraph.checkpoint.memory": lg_mem,
            "langgraph.prebuilt": lg_pre,
            "nova.tools.agent_tool_wrapper": atw_mod,
            "nova.llm.checkpoints": checkpoints_mod,
            "nova.models.CheckpointLink": checkpointlink_mod,
            "nova.models.Thread": thread_mod,
            "nova.models.UserFile": userfile_mod,
            "asgiref.sync": asgiref_sync,
        }
