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
            "asgiref.sync",
        ]
        for mod in mocked_modules:
            sys.modules.pop(mod, None)

    def create_mock_langchain_agent(self, return_value=None):
        """Factory for creating consistent mock LangChain agents."""
        class FakeLangchainAgent:
            def __init__(self):
                self.invocations = []

            async def ainvoke(self, payload, config=None, context=None):
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
        """
        Return a focused fake module dictionary for patching LLMAgent third-party dependencies.

        Design goals:
        - Only mock what nova.llm.llm_agent actually imports.
        - Provide minimal, stable surfaces so tests are resilient to internal refactors.
        - Avoid surprising interactions with unrelated modules.
        """
        # langchain_mistralai.chat_models.ChatMistralAI
        lc_mistral = types.ModuleType("langchain_mistralai")
        lc_mistral_chat = types.ModuleType("langchain_mistralai.chat_models")

        class ChatMistralAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_mistral_chat.ChatMistralAI = ChatMistralAI

        # langchain_ollama.chat_models.ChatOllama
        lc_ollama = types.ModuleType("langchain_ollama")
        lc_ollama_chat = types.ModuleType("langchain_ollama.chat_models")

        class ChatOllama:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_ollama_chat.ChatOllama = ChatOllama

        # langchain_openai.chat_models.ChatOpenAI
        lc_openai = types.ModuleType("langchain_openai")
        lc_openai_chat = types.ModuleType("langchain_openai.chat_models")

        class ChatOpenAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_openai_chat.ChatOpenAI = ChatOpenAI

        # langchain_core.messages.HumanMessage and ToolMessage
        lc_core = types.ModuleType("langchain_core")
        lc_core_msgs = types.ModuleType("langchain_core.messages")

        class HumanMessage:
            def __init__(self, content):
                self.content = content

        class ToolMessage:
            def __init__(self, name=None, artifact=None):
                self.name = name
                self.artifact = artifact

        lc_core_msgs.HumanMessage = HumanMessage
        lc_core_msgs.ToolMessage = ToolMessage

        # langchain.agents.create_agent
        lc_agents = types.ModuleType("langchain.agents")

        def create_agent(llm, tools=None, system_prompt=None, checkpointer=None):
            """
            Minimal fake agent that:
            - Records invocations (payload + config).
            - Returns a basic structure that LLMAgent.ainvoke can pass into extract_final_answer.
            """
            class DummyAgent:
                def __init__(self):
                    self.invocations = []

                async def ainvoke(self, payload, config=None, context=None):
                    # Mirror the shape expected by ainvoke: include the payload as "messages".
                    self.invocations.append((payload, config))
                    return {
                        "messages": [{"content": "final answer", "payload": payload}],
                    }

                def get_state(self, config=None):
                    return {"invocations": self.invocations}

            return DummyAgent()

        lc_agents.create_agent = create_agent

        # nova.llm.checkpoints.get_checkpointer
        checkpoints_mod = types.ModuleType("nova.llm.checkpoints")

        async def get_checkpointer():
            return MagicMock(name="FakeCheckpointer")

        checkpoints_mod.get_checkpointer = get_checkpointer

        # nova.models.CheckpointLink
        checkpointlink_mod = types.ModuleType("nova.models.CheckpointLink")

        class MockCheckpointLinkManager:
            def get_or_create(self, **kwargs):
                # Always return a fake checkpoint link with a deterministic id.
                link = SimpleNamespace(
                    checkpoint_id="fake_id",
                    id=1,
                    thread_id=kwargs.get("thread").id if kwargs.get("thread") else 1,
                    agent_id=getattr(kwargs.get("agent"), "id", 1),
                )
                return link, True

        class CheckpointLink:
            objects = MockCheckpointLinkManager()

        checkpointlink_mod.CheckpointLink = CheckpointLink

        # nova.models.UserFile
        userfile_mod = types.ModuleType("nova.models.UserFile")

        class UserFileManager:
            def filter(self, **kwargs):
                # Return an object with a configurable count() in tests via patching.
                return MagicMock(count=MagicMock(return_value=0))

        class UserFile:
            objects = UserFileManager()

        userfile_mod.UserFile = UserFile

        # asgiref.sync.sync_to_async
        asgiref_sync = types.ModuleType("asgiref.sync")

        def sync_to_async(func, **kwargs):
            async def wrapper(*args, **kw):
                return func(*args, **kw)
            return wrapper

        asgiref_sync.sync_to_async = sync_to_async

        # Return dict for patch.dict
        return {
            "langchain_mistralai": lc_mistral,
            "langchain_mistralai.chat_models": lc_mistral_chat,
            "langchain_ollama": lc_ollama,
            "langchain_ollama.chat_models": lc_ollama_chat,
            "langchain_openai": lc_openai,
            "langchain_openai.chat_models": lc_openai_chat,
            "langchain_core": lc_core,
            "langchain_core.messages": lc_core_msgs,
            "langchain.agents": lc_agents,
            "nova.llm.checkpoints": checkpoints_mod,
            "nova.models.CheckpointLink": checkpointlink_mod,
            "nova.models.UserFile": userfile_mod,
            "asgiref.sync": asgiref_sync,
        }
