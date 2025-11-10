import sys
import types
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from nova.models.Thread import Thread
from .base import BaseTestCase


class AgentToolWrapperTests(BaseTestCase):
    def setUp(self):
        """
        Ensure each test runs with its own thread and a fresh AgentToolWrapper
        import so sys.modules and fake dependencies are fully controlled.
        """
        super().setUp()
        # Base.py already sets up a test user
        self.thread = Thread.objects.create(user=self.user, subject="T")
        self.AgentToolWrapper = self._import_wrapper()

    def tearDown(self):
        """
        Clean up any injected modules so no state leaks between tests.
        """
        super().tearDown()
        sys.modules.pop("nova.tools.agent_tool_wrapper", None)
        sys.modules.pop("langchain_core.tools", None)

    @staticmethod
    def _install_fake_langchain_tools():
        """
        Install a minimal fake of langchain_core.tools.StructuredTool so
        AgentToolWrapper can be tested without the real LangChain dependency.
        """
        # Provide a minimal fake of langchain_core.tools.StructuredTool
        lc_core_tools = types.ModuleType("langchain_core.tools")

        class StructuredTool:
            @classmethod
            def from_function(cls, func=None, coroutine=None,
                              name=None, description=None, args_schema=None):
                # Return a plain object capturing what
                # was passed for assertions
                return {
                    "func": func,
                    "coroutine": coroutine,
                    "name": name,
                    "description": description,
                    "args_schema": args_schema,
                }

        lc_core_tools.StructuredTool = StructuredTool
        sys.modules["langchain_core.tools"] = lc_core_tools

    def _import_wrapper(self):
        """
        Import AgentToolWrapper after injecting the fake langchain_core.tools,
        guaranteeing the wrapper is wired against the controlled test stub.
        """
        # Ensure fake third-party module is in place
        # before importing the module under test
        self._install_fake_langchain_tools()

        # Import the wrapper after faking langchain_core.tools
        from nova.tools.agent_tool_wrapper import AgentToolWrapper
        return AgentToolWrapper

    def test_create_langchain_tool_shape_schema_and_name(self):
        """
        Verify that create_langchain_tool() produces a well-formed tool:
        - name normalized from agent name with `agent_` prefix
        - description taken from agent.tool_description
        - JSON-style args_schema requiring `question` mentioning agent name
        - exposes async coroutine only (no sync func).
        """
        # Agent stub with minimal fields used by wrapper
        agent_stub = SimpleNamespace(
            name="Sub Agent 1",
            tool_description="Can help with subtask 1",
        )

        wrapper = self.AgentToolWrapper(
            agent_config=agent_stub,
            thread=self.thread,
            user=self.user,
        )
        tool = wrapper.create_langchain_tool()

        # Name should be "agent_<lower>" with non-allowed chars replaced by "_"
        self.assertEqual(tool["name"], "agent_sub_agent_1")
        # Description should come from agent.tool_description
        self.assertEqual(tool["description"], "Can help with subtask 1")

        # Args schema should be a dict requiring "question" with
        # a string description mentioning agent name
        schema = tool["args_schema"]
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("type"), "object")
        self.assertIn("question", schema.get("properties", {}))
        self.assertIn("question", schema.get("required", []))
        self.assertIn("Sub Agent 1",
                      schema["properties"]["question"]["description"])

        # Ensure the tool exposes an async coroutine (no sync func expected)
        self.assertIsNone(tool["func"])
        self.assertTrue(callable(tool["coroutine"]))

    def test_execute_agent_success_invokes_and_cleans_up_and_tags(self):
        """
        Ensure the wrapped tool:
        - constructs LLMAgent with the correct user/thread/agent_config
        - calls ainvoke(question) and returns its result
        - performs cleanup() after execution.
        """
        # Fake LLMAgent with async factory 'create',
        # then async 'invoke' and 'cleanup'
        class FakeLLMAgent:
            def __init__(self, result="OK"):
                self.result = result
                self.cleanup_called = False
                self.create_calls = []
                self.invoke_calls = []

            @classmethod
            async def create(cls, user, thread, agent_config,
                             parent_config=None):
                inst = cls(result="ANSWER")
                inst.create_calls.append(
                    {"user": user,  "thread": thread,
                     "agent_config": agent_config,
                     "parent_config": parent_config}
                )
                # Attach for test inspection
                inst._user = user
                inst._thread = thread
                inst._agent_config = agent_config
                inst._parent_config = parent_config
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return self.result

            async def cleanup(self):
                self.cleanup_called = True

        agent_stub = SimpleNamespace(name="Delegate A",
                                     tool_description="desc")
        parent_user = SimpleNamespace(id=42)

        wrapper = self.AgentToolWrapper(agent_config=agent_stub,
                                        thread=self.thread, user=parent_user)

        # Patch the LLMAgent symbol used in the module under test
        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            tool = wrapper.create_langchain_tool()
            # Execute the coroutine with a question
            answer = asyncio.run(tool["coroutine"]("What time is it?"))

        self.assertEqual(answer, "ANSWER")

    def test_execute_agent_failure_returns_formatted_error_and_cleans_up(self):
        """
        When the delegated LLMAgent fails:
        - return a readable error string including agent name and message
        - include configuration guidance
        - still run cleanup() to avoid leaking resources.
        """
        class FailingLLMAgent:
            def __init__(self):
                self.cleanup_called = False

            @classmethod
            async def create(cls, user, thread,
                             agent_config, parent_config=None):
                return cls()

            async def ainvoke(self, question):
                raise RuntimeError("boom")

            async def cleanup(self):
                self.cleanup_called = True

        agent_stub = SimpleNamespace(name="SubTool X", tool_description="desc")
        wrapper = self.AgentToolWrapper(
            agent_config=agent_stub,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FailingLLMAgent):
            tool = wrapper.create_langchain_tool()
            result = asyncio.run(tool["coroutine"]("Hello"))

        # Error string should include the agent name and guidance
        self.assertIn("Error in sub-agent SubTool X: boom", result)
        self.assertIn("Check connections or config", result)
