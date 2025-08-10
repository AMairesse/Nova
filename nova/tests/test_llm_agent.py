import sys
import types
import importlib
import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

# We import Django models only for the ProviderType enum; no DB operations are needed.
from nova.models import ProviderType


class LLMAgentTests(TestCase):
    @staticmethod
    def _install_fake_third_party_modules():
        # langchain_* chat models
        lc_mistral = types.ModuleType("langchain_mistralai")
        lc_mistral_chat = types.ModuleType("langchain_mistralai.chat_models")

        class ChatMistralAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_mistral_chat.ChatMistralAI = ChatMistralAI
        sys.modules["langchain_mistralai"] = lc_mistral
        sys.modules["langchain_mistralai.chat_models"] = lc_mistral_chat

        lc_ollama = types.ModuleType("langchain_ollama")
        lc_ollama_chat = types.ModuleType("langchain_ollama.chat_models")

        class ChatOllama:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_ollama_chat.ChatOllama = ChatOllama
        sys.modules["langchain_ollama"] = lc_ollama
        sys.modules["langchain_ollama.chat_models"] = lc_ollama_chat

        lc_openai = types.ModuleType("langchain_openai")
        lc_openai_chat = types.ModuleType("langchain_openai.chat_models")

        class ChatOpenAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        lc_openai_chat.ChatOpenAI = ChatOpenAI
        sys.modules["langchain_openai"] = lc_openai
        sys.modules["langchain_openai.chat_models"] = lc_openai_chat

        # langchain_core.messages
        lc_core = types.ModuleType("langchain_core")
        lc_core_msgs = types.ModuleType("langchain_core.messages")

        class HumanMessage:
            def __init__(self, content):
                self.content = content

        class AIMessage:
            def __init__(self, content):
                self.content = content

        lc_core_msgs.HumanMessage = HumanMessage
        lc_core_msgs.AIMessage = AIMessage
        sys.modules["langchain_core"] = lc_core
        sys.modules["langchain_core.messages"] = lc_core_msgs

        # langchain_core.tools
        lc_core_tools = types.ModuleType("langchain_core.tools")

        class StructuredTool:
            @classmethod
            def from_function(cls, func, coroutine=None, name=None, description=None, args_schema=None):
                # Return a simple shape that we can assert on
                return {"name": name, "description": description, "args_schema": args_schema, "func": func, "coroutine": coroutine}

        lc_core_tools.StructuredTool = StructuredTool
        sys.modules["langchain_core.tools"] = lc_core_tools

        # langchain_core.callbacks
        lc_core_cb = types.ModuleType("langchain_core.callbacks")

        class BaseCallbackHandler:
            pass

        lc_core_cb.BaseCallbackHandler = BaseCallbackHandler
        sys.modules["langchain_core.callbacks"] = lc_core_cb

        # langgraph
        lg_root = types.ModuleType("langgraph")
        lg_mem = types.ModuleType("langgraph.checkpoint.memory")

        class MemorySaver:
            pass

        lg_mem.MemorySaver = MemorySaver
        sys.modules["langgraph"] = lg_root
        sys.modules["langgraph.checkpoint"] = types.ModuleType("langgraph.checkpoint")
        sys.modules["langgraph.checkpoint.memory"] = lg_mem

        lg_pre = types.ModuleType("langgraph.prebuilt")

        def create_react_agent(llm, tools=None, prompt=None, checkpointer=None):
            class DummyAgent:
                def __init__(self):
                    # Keep last state for debugging purposes
                    self.state = []

                def update_state(self, config, payload):
                    self.state.append((config, payload))

                async def ainvoke(self, payload, config=None):
                    # Echo payload as result to allow extract_final_answer to be called
                    return payload
            return DummyAgent()

        lg_pre.create_react_agent = create_react_agent
        sys.modules["langgraph.prebuilt"] = lg_pre

        # Fake nova.tools.agent_tool_wrapper (used when has_agent_tools is True)
        atw_mod = types.ModuleType("nova.tools.agent_tool_wrapper")

        class AgentToolWrapper:
            def __init__(self, agent_tool, user, parent_config=None):
                self.agent_tool = agent_tool
                self.user = user
                self.parent_config = parent_config

            def create_langchain_tool(self):
                return {"wrapped_agent_tool": getattr(self.agent_tool, "name", "unknown")}

        atw_mod.AgentToolWrapper = AgentToolWrapper
        sys.modules["nova.tools.agent_tool_wrapper"] = atw_mod

    def _import_llm_agent(self):
        # Ensure third-party fakes are in place before importing
        self._install_fake_third_party_modules()

        # Import the module under test with a robust path resolution
        try:
            mod = importlib.import_module("nova.llm.agent")
        except Exception:
            mod = importlib.import_module("nova.llm_agent")
        return mod

    # ---------------- build_system_prompt ----------------

    def test_build_system_prompt_default_and_template(self):
        llm_agent_mod = self._import_llm_agent()
        LLMAgent = llm_agent_mod.LLMAgent

        agent = LLMAgent(
            user=SimpleNamespace(id=1),
            thread_id="t1",
            system_prompt=None,
        )
        default_prompt = agent.build_system_prompt()
        self.assertIn("You are a helpful assistant", default_prompt)

        # With a template using {today}
        agent2 = LLMAgent(
            user=SimpleNamespace(id=2),
            thread_id="t2",
            system_prompt="Today is {today}.",
        )
        templated = agent2.build_system_prompt()
        self.assertNotIn("{today}", templated)
        self.assertTrue(templated.startswith("Today is "))

    # ---------------- create_llm_agent ----------------

    def test_create_llm_agent_factory_and_errors(self):
        llm_agent_mod = self._import_llm_agent()
        LLMAgent = llm_agent_mod.LLMAgent

        # Happy path: OPENAI provider -> returns instance of our fake ChatOpenAI
        provider = SimpleNamespace(provider_type=ProviderType.OPENAI, model="m", api_key="k", base_url=None)
        agent = LLMAgent(
            user=SimpleNamespace(id=1),
            thread_id="t",
            system_prompt=None,
            llm_provider=provider,
        )
        agent.django_agent = object()  # truthy so the method does not raise
        obj = agent.create_llm_agent()
        self.assertEqual(obj.__class__.__name__, "ChatOpenAI")

        # Error when provider not configured
        agent2 = LLMAgent(
            user=SimpleNamespace(id=1),
            thread_id="t",
            system_prompt=None,
            llm_provider=None,
        )
        agent2.django_agent = object()
        with self.assertRaises(Exception):
            agent2.create_llm_agent()

        # Unsupported provider type
        agent3 = LLMAgent(
            user=SimpleNamespace(id=1),
            thread_id="t",
            system_prompt=None,
            llm_provider=SimpleNamespace(provider_type="UNKNOWN"),
        )
        agent3.django_agent = object()
        with self.assertRaises(ValueError):
            agent3.create_llm_agent()

    # ---------------- cleanup ----------------

    def test_cleanup_calls_close_on_loaded_modules(self):
        llm_agent_mod = self._import_llm_agent()
        LLMAgent = llm_agent_mod.LLMAgent

        class BuiltinModule:
            def __init__(self):
                self.closed = False

            async def close(self, agent):
                self.closed = True

        agent = LLMAgent(user=SimpleNamespace(id=1), thread_id="t")
        m1, m2 = BuiltinModule(), BuiltinModule()
        agent._loaded_builtin_modules = [m1, m2]

        asyncio.run(agent.cleanup())
        self.assertTrue(m1.closed)
        self.assertTrue(m2.closed)

    # ---------------- _load_agent_tools ----------------

    def test_load_agent_tools_builtin_mcp_and_agent_tool(self):
        llm_agent_mod = self._import_llm_agent()
        LLMAgent = llm_agent_mod.LLMAgent

        # Fake builtin module returned by nova.tools.import_module
        class FakeBuiltinModule:
            def __init__(self):
                self.init_called = False

            async def init(self, agent):
                self.init_called = True

            async def get_functions(self, tool, agent):
                # Return a simple list of tools
                return [{"builtin_tool": tool.tool_subtype}]

            async def close(self, agent):
                # not exercised here
                pass

        builtin_module = FakeBuiltinModule()

        # Install fake nova.tools with import_module returning our module
        fake_nova_tools = types.ModuleType("nova.tools")

        def fake_import_module(python_path):
            # In real code, python_path directs which module to load; here we always return our fake
            return builtin_module

        fake_nova_tools.import_module = fake_import_module
        sys.modules["nova.tools"] = fake_nova_tools

        # Install fake MCP client module
        fake_mcp_client_mod = types.ModuleType("nova.mcp.client")

        class FakeMCPClient:
            def __init__(self, endpoint, thread_id=None, credential=None, transport_type=None, user_id=None):
                self.endpoint = endpoint

            def list_tools(self, force_refresh=False):
                # Return metadata as dicts (what LLMAgent expects)
                return [
                    {"name": "do thing", "description": "desc", "input_schema": {}},
                    {"name": "calc:add", "description": "", "input_schema": {"type": "object"}},
                ]

            async def acall(self, tool_name, **kwargs):
                return {"ok": True}

            def call(self, tool_name, **kwargs):
                return {"ok": True}

        fake_mcp_client_mod.MCPClient = FakeMCPClient
        sys.modules["nova.mcp.client"] = fake_mcp_client_mod

        # Patch StructuredTool in the imported module to a fake that returns a dict
        class FakeStructuredTool:
            @classmethod
            def from_function(cls, func, coroutine=None, name=None, description=None, args_schema=None):
                return {"wrapped_name": name, "description": description, "args_schema": args_schema, "func": func, "coroutine": coroutine}

        # Prepare inputs for LLMAgent
        builtin_tools = [SimpleNamespace(python_path="nova.tools.builtins.date", tool_subtype="date", is_active=True)]
        mcp_tool_obj = SimpleNamespace(endpoint="https://mcp.example.com", transport_type=None)
        # (tool, cred, cached_func_metas, cred_user_id)
        mcp_tools_data = [(mcp_tool_obj, SimpleNamespace(user=SimpleNamespace(id=1)), None, 1)]
        agent_tools = [SimpleNamespace(name="delegate_agent")]

        agent = LLMAgent(
            user=SimpleNamespace(id=99),
            thread_id="T123",
            builtin_tools=builtin_tools,
            mcp_tools_data=mcp_tools_data,
            agent_tools=agent_tools,
            has_agent_tools=True,
            system_prompt=None,
            llm_provider=SimpleNamespace(provider_type=ProviderType.OPENAI, model="m", api_key="k"),
        )

        # Swap StructuredTool in module namespace
        original_struct_tool = llm_agent_mod.StructuredTool
        llm_agent_mod.StructuredTool = FakeStructuredTool
        try:
            tools = asyncio.run(agent._load_agent_tools())
        finally:
            # Restore to avoid side effects on other tests
            llm_agent_mod.StructuredTool = original_struct_tool

        # Assertions:
        # - Builtin tool loaded and module tracked
        self.assertTrue(any(isinstance(mod, FakeBuiltinModule) for mod in agent._loaded_builtin_modules))
        self.assertIn({"builtin_tool": "date"}, tools)

        # - MCP tools wrapped with sanitized name:
        #   "do thing" -> "do_thing", "calc:add" -> "calc_add"
        wrapped_names = {t.get("wrapped_name") for t in tools if "wrapped_name" in t}
        self.assertIn("do_thing", wrapped_names)
        self.assertIn("calc_add", wrapped_names)
        # - args_schema None for empty dict, dict otherwise
        for t in tools:
            if t.get("wrapped_name") == "do_thing":
                self.assertIsNone(t.get("args_schema"))
            if t.get("wrapped_name") == "calc_add":
                self.assertIsInstance(t.get("args_schema"), dict)

        # - AgentToolWrapper integration (from fake module)
        self.assertIn({"wrapped_agent_tool": "delegate_agent"}, tools)

    # ---------------- invoke ----------------

    def test_invoke_awaits_and_extracts_final_answer(self):
        llm_agent_mod = self._import_llm_agent()
        LLMAgent = llm_agent_mod.LLMAgent

        # Patch extract_final_answer in module namespace
        original_extract = llm_agent_mod.extract_final_answer
        llm_agent_mod.extract_final_answer = lambda output: "FINAL"
        try:
            # Fake agent with async ainvoke
            class FakeAgent:
                def __init__(self):
                    self.invocations = []

                async def ainvoke(self, payload, config=None):
                    self.invocations.append((payload, config))
                    return {"messages": ["ignored"]}

            agent = LLMAgent(user=SimpleNamespace(id=1), thread_id="t", system_prompt=None, llm_provider=SimpleNamespace(provider_type=ProviderType.OPENAI))
            agent.agent = FakeAgent()

            out = asyncio.run(agent.invoke("Hello", silent_mode=True))
            self.assertEqual(out, "FINAL")
            # Should have used silent_config when silent_mode=True
            self.assertEqual(len(agent.agent.invocations), 1)
            _payload, used_config = agent.agent.invocations[0]
            self.assertIs(used_config, agent.silent_config)
        finally:
            llm_agent_mod.extract_final_answer = original_extract
