# nova/tests/test_llm_agent.py
import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock, MagicMock

from nova.models.models import ProviderType
import nova.llm.llm_agent as llm_agent_mod


class LLMAgentTests(IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        # Any per-test setup can go here

    def tearDown(self):
        # Explicitly clean up any potentially lingering modules after each test
        mocked_modules = [
            "langchain_mistralai", "langchain_mistralai.chat_models",
            "langchain_ollama", "langchain_ollama.chat_models",
            "langchain_openai", "langchain_openai.chat_models",
            "langchain_core", "langchain_core.messages",
            "langgraph", "langgraph.checkpoint", "langgraph.checkpoint.memory",
            "langgraph.prebuilt",
            "nova.tools.agent_tool_wrapper",
            "nova.tools", "nova.mcp.client", "nova.tools.files",
            "nova.llm.checkpoints",  # Add checkpoints mock
            "nova.models.models", "nova.models.Thread",  # ORM-related
            "asgiref.sync",  # For sync_to_async
        ]
        for mod in mocked_modules:
            sys.modules.pop(mod, None)
        super().tearDown()

    def _get_fake_third_party_modules(self):
        # Define all fake modules here (extracted from original _install_fake_third_party_modules)
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

        # Fake nova.tools.agent_tool_wrapper (used when has_agent_tools is True)
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
            return MagicMock()  # Fake checkpointer

        checkpoints_mod.get_checkpointer = get_checkpointer

        # Fake models and Thread
        models_mod = types.ModuleType("nova.models.models")
        thread_mod = types.ModuleType("nova.models.Thread")

        # Mock CheckpointLink with proper Django manager pattern
        mock_checkpoint_link = SimpleNamespace(checkpoint_id="fake_id", id=1, thread_id=1, agent_id=1)

        class MockQuerySet:
            def get_or_create(self, **kwargs):
                # Return the mock object and created flag
                return (mock_checkpoint_link, True)

        class MockManager:
            def get_queryset(self):
                return MockQuerySet()

        class CheckpointLink:
            objects = MockManager()

        models_mod.CheckpointLink = CheckpointLink
        models_mod.UserFile = MagicMock()  # For file counting in ainvoke

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
            "nova.models.models": models_mod,
            "nova.models.Thread": thread_mod,
            "asgiref.sync": asgiref_sync,
        }

    # ---------------- build_system_prompt ----------------

    async def test_build_system_prompt_default_and_template(self):
        fakes = self._get_fake_third_party_modules()
        with patch.dict(sys.modules, fakes):
            agent = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=1),
                thread=SimpleNamespace(id="t1"),
                langgraph_thread_id="fake_id",
                agent_config=None,
            )
            default_prompt = await agent.build_system_prompt()
            self.assertIn("You are a helpful assistant", default_prompt)

            # With a template using {today}
            agent2 = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=2),
                thread=SimpleNamespace(id="t2"),
                langgraph_thread_id="fake_id",
                agent_config=None,
                system_prompt="Today is {today}.",
            )
            templated = await agent2.build_system_prompt()
            self.assertNotIn("{today}", templated)
            self.assertTrue(templated.startswith("Today is "))

    # ---------------- create_llm_agent ----------------

    def test_create_llm_agent_factory_and_errors(self):
        fakes = self._get_fake_third_party_modules()
        with patch.dict(sys.modules, fakes):
            # Happy path: OPENAI provider -> returns instance of our fake ChatOpenAI
            provider = SimpleNamespace(provider_type=ProviderType.OPENAI, model="m", api_key="k", base_url=None)
            agent = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=1),
                thread=SimpleNamespace(id="t"),
                langgraph_thread_id="fake_id",
                agent_config=SimpleNamespace(),  # truthy
                system_prompt=None,
                llm_provider=provider,
            )
            obj = agent.create_llm_agent()
            self.assertEqual(obj.__class__.__name__, "ChatOpenAI")

            # Error when provider not configured
            agent2 = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=1),
                thread=SimpleNamespace(id="t"),
                langgraph_thread_id="fake_id",
                agent_config=SimpleNamespace(),
                system_prompt=None,
                llm_provider=None,
            )
            with self.assertRaises(Exception):
                agent2.create_llm_agent()

            # Unsupported provider type
            agent3 = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=1),
                thread=SimpleNamespace(id="t"),
                langgraph_thread_id="fake_id",
                agent_config=SimpleNamespace(),
                system_prompt=None,
                llm_provider=SimpleNamespace(provider_type="UNKNOWN"),
            )
            with self.assertRaises(ValueError):
                agent3.create_llm_agent()

    # ---------------- cleanup ----------------

    async def test_cleanup_calls_close_on_loaded_modules(self):
        fakes = self._get_fake_third_party_modules()
        with patch.dict(sys.modules, fakes):
            class BuiltinModule:
                def __init__(self):
                    self.closed = False

                async def close(self, agent):
                    self.closed = True

            agent = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=1),
                thread=SimpleNamespace(id="t"),
                langgraph_thread_id="fake_id",
                agent_config=None,
            )
            m1, m2 = BuiltinModule(), BuiltinModule()
            agent._loaded_builtin_modules = [m1, m2]

            await agent.cleanup()
            self.assertTrue(m1.closed)
            self.assertTrue(m2.closed)

    # ---------------- load_tools (via llm_tools.py) ----------------

    async def test_load_tools_builtin_mcp_and_agent_tool(self):
        fakes = self._get_fake_third_party_modules()

        # Additional fakes specific to this test (nova.tools and nova.mcp.client)
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

            async def alist_tools(self, force_refresh=False):
                # Async version for real code that calls alist_tools [[5]]
                return self.list_tools(force_refresh=force_refresh)

            async def acall(self, tool_name, **kwargs):
                return {"ok": True}

            def call(self, tool_name, **kwargs):
                return {"ok": True}

        fake_mcp_client_mod.MCPClient = FakeMCPClient

        # Fake files module for file tools
        fake_files_mod = types.ModuleType("nova.tools.files")

        async def get_functions(agent):
            return [{"file_tool": True}]

        fake_files_mod.get_functions = get_functions

        # Fake langchain_core.tools for StructuredTool
        lc_core_tools = types.ModuleType("langchain_core.tools")

        class StructuredTool:
            @classmethod
            def from_function(cls, func, coroutine=None, name=None,
                              description=None, args_schema=None):
                # Return a simple shape that we can assert on
                return {"name": name, "description": description,
                        "args_schema": args_schema, "func": func,
                        "coroutine": coroutine}

        lc_core_tools.StructuredTool = StructuredTool

        # Merge additional fakes into the dict
        fakes.update({
            "nova.tools": fake_nova_tools,
            "nova.mcp.client": fake_mcp_client_mod,
            "nova.tools.files": fake_files_mod,
            "langchain_core.tools": lc_core_tools,
        })

        with patch.dict(sys.modules, fakes):
            # Prepare inputs for LLMAgent
            builtin_tools = [SimpleNamespace(python_path="nova.tools.builtins.date", tool_subtype="date",
                                             is_active=True, tool_type="BUILTIN")]
            mcp_tool_obj = SimpleNamespace(endpoint="https://mcp.example.com", transport_type=None)
            # (tool, cred, cached_func_metas, cred_user_id)
            mcp_tools_data = [(mcp_tool_obj, SimpleNamespace(user=SimpleNamespace(id=1)), None, 1)]
            agent_tools = [SimpleNamespace(name="delegate_agent")]

            agent = llm_agent_mod.LLMAgent(
                user=SimpleNamespace(id=99),
                thread=SimpleNamespace(id="T123"),
                langgraph_thread_id="fake_id",
                agent_config=SimpleNamespace(),
                builtin_tools=builtin_tools,
                mcp_tools_data=mcp_tools_data,
                agent_tools=agent_tools,
                has_agent_tools=True,
                system_prompt=None,
                llm_provider=SimpleNamespace(provider_type=ProviderType.OPENAI, model="m", api_key="k"),
            )

            # Call load_tools directly (as in create)
            tools = await llm_agent_mod.load_tools(agent)

            # Assertions:
            # - Builtin tool loaded and module tracked
            self.assertTrue(any(isinstance(mod, FakeBuiltinModule) for mod in agent._loaded_builtin_modules))
            self.assertIn({"builtin_tool": "date"}, tools)

            # - MCP tools wrapped with sanitized name:
            #   "do thing" -> "do_thing", "calc:add" -> "calc_add"
            wrapped_names = set()
            for t in tools:
                if hasattr(t, 'name'):  # StructuredTool objects have name attribute
                    wrapped_names.add(t.name)
                elif isinstance(t, dict) and "name" in t:  # Dict tools have name key
                    wrapped_names.add(t["name"])

            self.assertIn("do_thing", wrapped_names)
            self.assertIn("calc_add", wrapped_names)
            # - args_schema is a pydantic model for empty dict, dict otherwise
            for t in tools:
                if hasattr(t, 'name') and t.name == "do_thing":
                    # Empty input schema creates a pydantic model, not None
                    self.assertTrue(t.args_schema is not None)
                if hasattr(t, 'name') and t.name == "calc_add":
                    # Non-empty input schema should be preserved
                    self.assertEqual(t.args_schema, {"type": "object"})

            # - AgentToolWrapper integration (from fake module)
            self.assertIn({"wrapped_agent_tool": "delegate_agent"}, tools)

            # - File tools loaded
            self.assertIn({"file_tool": True}, tools)

    # ---------------- ainvoke ----------------

    async def test_ainvoke_awaits_and_extracts_final_answer_with_file_context(self):
        fakes = self._get_fake_third_party_modules()
        with patch.dict(sys.modules, fakes):
            # Fake langchain_agent with async ainvoke
            class FakeLangchainAgent:
                def __init__(self):
                    self.invocations = []

                async def ainvoke(self, payload, config=None):
                    self.invocations.append((payload, config))
                    return {"messages": [{"content": "final answer"}]}

            # Patch extract_final_answer in module namespace
            with patch.object(llm_agent_mod, "extract_final_answer", lambda output: "FINAL"):
                # Mock UserFile.objects.filter and count
                mock_filter = MagicMock()
                mock_filter.count = lambda: 2  # Simulate 2 files
                llm_agent_mod.UserFile.objects.filter = lambda **kw: mock_filter

                agent = llm_agent_mod.LLMAgent(
                    user=SimpleNamespace(id=1),
                    thread=SimpleNamespace(id="t"),
                    langgraph_thread_id="fake_id",
                    agent_config=None,
                    system_prompt=None,
                    llm_provider=SimpleNamespace(provider_type=ProviderType.OPENAI),
                )
                agent.langchain_agent = FakeLangchainAgent()

                out = await agent.ainvoke("Hello", silent_mode=True)
                self.assertEqual(out, "FINAL")
                # Should have used silent_config when silent_mode=True
                self.assertEqual(len(agent.langchain_agent.invocations), 1)
                payload, used_config = agent.langchain_agent.invocations[0]
                self.assertIs(used_config, agent.silent_config)
                # Check file context added to system prompt
                system_prompt = await agent.build_system_prompt()
                self.assertIn("There is 2 attached files. Use file tools if needed.", system_prompt)

    # ---------------- create (class method) ----------------

    async def test_create_initializes_with_pre_fetched_data_and_tools(self):
        fakes = self._get_fake_third_party_modules()
        with patch.dict(sys.modules, fakes):
            # Mock fetch_user_params_sync and fetch_agent_data_sync
            with patch.object(llm_agent_mod.LLMAgent, "fetch_user_params_sync", return_value=(False, None, None, None)):
                with patch.object(llm_agent_mod.LLMAgent, "fetch_agent_data_sync",
                                  return_value=([], [], [], False, "prompt", 25,
                                                SimpleNamespace(provider_type=ProviderType.OPENAI, model="gpt-4",
                                                                api_key="fake_key", base_url=None))):
                    # Mock load_tools to return fake tools
                    with patch("nova.llm.llm_agent.load_tools", AsyncMock(return_value=[{"tool": True}])):
                        # Mock get_checkpointer to return a fake checkpointer
                        with patch("nova.llm.llm_agent.get_checkpointer", AsyncMock(return_value=MagicMock())):
                            # Mock create_react_agent to return a fake agent
                            with patch("nova.llm.llm_agent.create_react_agent", return_value=MagicMock()):
                                # Mock CheckpointLink.objects.get_or_create directly
                                mock_checkpoint_link = SimpleNamespace(checkpoint_id="fake_id")
                                with patch.object(llm_agent_mod.CheckpointLink.objects, "get_or_create",
                                                  return_value=(mock_checkpoint_link, True)):
                                    user = SimpleNamespace(id=1, userparameters=SimpleNamespace(allow_langfuse=False))
                                    thread = SimpleNamespace(id="t")
                                    agent = await llm_agent_mod.LLMAgent.create(user, thread, SimpleNamespace())

                        # Verify the agent was created successfully
                        self.assertIsNotNone(agent)
                        self.assertIsInstance(agent, llm_agent_mod.LLMAgent)
                        self.assertIsNotNone(agent.langchain_agent)  # React agent created
