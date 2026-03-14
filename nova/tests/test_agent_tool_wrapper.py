import sys
import types
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.factories import create_agent, create_provider


User = get_user_model()


class AgentToolWrapperTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        """
        Ensure each test runs with its own thread and a fresh AgentToolWrapper
        import so sys.modules and fake dependencies are fully controlled.
        """
        super().setUp()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
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
            def from_function(
                cls,
                func=None,
                coroutine=None,
                name=None,
                description=None,
                args_schema=None,
                **kwargs,
            ):
                # Return a plain object capturing what
                # was passed for assertions
                return {
                    "func": func,
                    "coroutine": coroutine,
                    "name": name,
                    "description": description,
                    "args_schema": args_schema,
                    **kwargs,
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
        self.assertIn("artifact_ids", schema.get("properties", {}))
        self.assertIn("file_ids", schema.get("properties", {}))
        self.assertIn("output_mode", schema.get("properties", {}))
        self.assertIn(
            "Use only IDs returned by artifact_ls or artifact_search.",
            schema["properties"]["artifact_ids"]["description"],
        )
        self.assertIn(
            "Use only IDs returned by file_ls.",
            schema["properties"]["file_ids"]["description"],
        )

        # Ensure the tool exposes an async coroutine (no sync func expected)
        self.assertIsNone(tool["func"])
        self.assertTrue(callable(tool["coroutine"]))
        self.assertTrue(tool["return_direct"])
        self.assertEqual(tool["response_format"], "content_and_artifact")

    def test_execute_agent_success_invokes_and_cleans_up_and_tags(self):
        """
        Ensure the wrapped tool:
        - constructs LLMAgent with the correct user/thread/agent_config
        - calls ainvoke(question) and returns its result
        - performs cleanup_runtime() after execution.
        """
        # Fake LLMAgent with async factory 'create',
        # then async 'invoke' and 'cleanup'
        class FakeLLMAgent:
            instances = []
            generated_artifact_id = None

            def __init__(self, result="OK"):
                self.result = result
                self.cleanup_called = False
                self.invoke_calls = []
                self.last_generated_tool_artifact_refs = []

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                inst = cls(result="ANSWER")
                inst._user = user
                inst._thread = thread
                inst._agent_config = agent_config
                inst._callbacks = callbacks
                inst._tools_enabled = tools_enabled
                if cls.generated_artifact_id is not None:
                    inst.last_generated_tool_artifact_refs = [
                        {"artifact_id": cls.generated_artifact_id}
                    ]
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return self.result

            async def cleanup_runtime(self):
                self.cleanup_called = True

        source_message = self.thread.add_message("Existing source", actor=Actor.USER)
        source_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=source_message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.IMAGE,
            label="source-image.png",
            mime_type="image/png",
        )
        generated_message = self.thread.add_message("Generated", actor=Actor.AGENT)
        generated_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=generated_message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="generated-image.png",
            mime_type="image/png",
        )

        agent_stub = SimpleNamespace(
            name="Delegate A",
            tool_description="desc",
            llm_provider=None,
            id=17,
        )

        wrapper = self.AgentToolWrapper(
            agent_config=agent_stub,
            thread=self.thread,
            user=self.user,
        )

        # Patch the LLMAgent symbol used in the module under test
        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            FakeLLMAgent.generated_artifact_id = generated_artifact.id
            tool = wrapper.create_langchain_tool()
            answer, artifact_payload = asyncio.run(
                tool["coroutine"](
                    "What time is it?",
                    artifact_ids=[source_artifact.id],
                    output_mode="image",
                )
            )
        self.assertEqual(answer, "ANSWER")
        self.assertEqual(
            artifact_payload,
            {"artifact_ids": [generated_artifact.id], "tool_output": True},
        )
        self.assertTrue(FakeLLMAgent.instances[-1].cleanup_called)
        self.assertTrue(FakeLLMAgent.instances[-1]._tools_enabled)
        self.assertTrue(FakeLLMAgent.instances[-1].invoke_calls)

        hidden_message = (
            self.thread.get_messages()
            .filter(actor=Actor.SYSTEM, internal_data__hidden_subagent_trace=True)
            .latest("id")
        )
        self.assertEqual(hidden_message.internal_data["response_mode"], "image")
        cloned_input = MessageArtifact.objects.get(
            message=hidden_message,
            direction=ArtifactDirection.INPUT,
            source_artifact=source_artifact,
        )
        self.assertEqual(cloned_input.kind, ArtifactKind.IMAGE)

    def test_execute_agent_accepts_thread_file_ids_and_clones_them_as_input_artifacts(self):
        class FakeLLMAgent:
            instances = []

            def __init__(self, result="OK"):
                self.result = result
                self.cleanup_called = False
                self.invoke_calls = []
                self.last_generated_tool_artifact_refs = []

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                inst = cls(result="ANSWER")
                inst._tools_enabled = tools_enabled
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return self.result

            async def cleanup_runtime(self):
                self.cleanup_called = True

        provider = create_provider(self.user, name="media-provider")
        thread_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}/image.png",
            original_filename="/image.png",
            mime_type="image/png",
            size=512,
            scope=UserFile.Scope.THREAD_SHARED,
        )
        agent = create_agent(
            self.user,
            provider,
            name="Image sub-agent",
            is_tool=True,
            tool_description="Modify images",
        )
        wrapper = self.AgentToolWrapper(
            agent_config=agent,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            with patch(
                "nova.tools.agent_tool_wrapper.invoke_native_provider_for_message",
                return_value=None,
            ):
                with patch(
                    "nova.tools.agent_tool_wrapper.download_file_content",
                    return_value=b"fake-image",
                ):
                    tool = wrapper.create_langchain_tool()
                    answer, artifact_payload = asyncio.run(
                        tool["coroutine"](
                            "Please modify this image.",
                            file_ids=[thread_file.id],
                            output_mode="image",
                        )
                    )

        self.assertEqual(answer, "ANSWER")
        self.assertEqual(artifact_payload, {})
        invoke_payload = FakeLLMAgent.instances[-1].invoke_calls[0]
        self.assertIsInstance(invoke_payload, list)
        self.assertEqual(invoke_payload[0]["type"], "text")
        self.assertIn("Attached artifacts:", invoke_payload[0]["text"])
        self.assertIn("image.png", invoke_payload[0]["text"])
        self.assertEqual(invoke_payload[1]["type"], "image")
        self.assertEqual(invoke_payload[1]["filename"], "image.png")
        hidden_message = (
            self.thread.get_messages()
            .filter(actor=Actor.SYSTEM, internal_data__hidden_subagent_trace=True)
            .latest("id")
        )
        cloned_input = MessageArtifact.objects.get(
            message=hidden_message,
            direction=ArtifactDirection.INPUT,
            user_file=thread_file,
        )
        self.assertEqual(cloned_input.kind, ArtifactKind.IMAGE)
        self.assertEqual(cloned_input.metadata.get("source"), "thread_file")

    def test_execute_agent_accepts_artifact_ids_accidentally_passed_via_file_ids(self):
        class FakeLLMAgent:
            instances = []

            def __init__(self, result="OK"):
                self.result = result
                self.cleanup_called = False
                self.invoke_calls = []
                self.last_generated_tool_artifact_refs = []

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                inst = cls(result="ANSWER")
                inst._tools_enabled = tools_enabled
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return self.result

            async def cleanup_runtime(self):
                self.cleanup_called = True

        provider = create_provider(self.user, name="media-provider")
        source_message = self.thread.add_message("Source", actor=Actor.USER)
        source_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=source_message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.IMAGE,
            mime_type="image/png",
            label="input-image.png",
        )
        agent = create_agent(
            self.user,
            provider,
            name="Image sub-agent",
            is_tool=True,
            tool_description="Modify images",
        )
        wrapper = self.AgentToolWrapper(
            agent_config=agent,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            with patch(
                "nova.tools.agent_tool_wrapper.invoke_native_provider_for_message",
                return_value=None,
            ):
                tool = wrapper.create_langchain_tool()
                answer, artifact_payload = asyncio.run(
                    tool["coroutine"](
                        "Please modify this image.",
                        file_ids=[source_artifact.id],
                        output_mode="image",
                    )
                )

        self.assertEqual(answer, "ANSWER")
        self.assertEqual(artifact_payload, {})
        hidden_message = (
            self.thread.get_messages()
            .filter(actor=Actor.SYSTEM, internal_data__hidden_subagent_trace=True)
            .latest("id")
        )
        cloned_input = MessageArtifact.objects.get(
            message=hidden_message,
            direction=ArtifactDirection.INPUT,
            source_artifact=source_artifact,
        )
        self.assertEqual(cloned_input.kind, ArtifactKind.IMAGE)
        self.assertEqual(cloned_input.metadata.get("source"), "artifact_id_fallback")
        self.assertEqual(cloned_input.metadata.get("requested_via"), "file_ids")

    def test_execute_agent_invalid_file_ids_error_points_to_file_ls_and_artifacts(self):
        class FakeLLMAgent:
            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                raise AssertionError("LLMAgent.create should not be called when input attachment fails")

        provider = create_provider(self.user, name="media-provider")
        agent = create_agent(
            self.user,
            provider,
            name="Image sub-agent",
            is_tool=True,
            tool_description="Modify images",
        )
        wrapper = self.AgentToolWrapper(
            agent_config=agent,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            tool = wrapper.create_langchain_tool()
            result, artifact_payload = asyncio.run(
                tool["coroutine"](
                    "Please modify this image.",
                    file_ids=[9999],
                    output_mode="image",
                )
            )

        self.assertIn("Call file_ls to discover valid file_ids.", result)
        self.assertIn("use artifact_ls or artifact_search and pass artifact_ids instead", result)
        self.assertEqual(artifact_payload, {})

    def test_execute_agent_failure_returns_formatted_error_and_cleans_up(self):
        """
        When the delegated LLMAgent fails:
        - return a readable error string including agent name and message
        - include configuration guidance
        - still run cleanup_runtime() to avoid leaking resources.
        """
        class FailingLLMAgent:
            instances = []

            def __init__(self):
                self.cleanup_called = False

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                inst = cls()
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                raise RuntimeError("boom")

            async def cleanup_runtime(self):
                self.cleanup_called = True

        agent_stub = SimpleNamespace(
            name="SubTool X",
            tool_description="desc",
            llm_provider=None,
            id=29,
        )
        wrapper = self.AgentToolWrapper(
            agent_config=agent_stub,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FailingLLMAgent):
            tool = wrapper.create_langchain_tool()
            result, artifact_payload = asyncio.run(tool["coroutine"]("Hello"))

        # Error string should include the agent name and guidance
        self.assertIn("Error in sub-agent SubTool X: boom", result)
        self.assertIn("Check connections or config", result)
        self.assertEqual(artifact_payload, {})
        self.assertTrue(FailingLLMAgent.instances[-1].cleanup_called)

    def test_execute_agent_loads_real_provider_without_sync_orm_in_async_context(self):
        class FakeLLMAgent:
            instances = []

            def __init__(self, result="OK"):
                self.result = result
                self.cleanup_called = False
                self.invoke_calls = []
                self.last_generated_tool_artifact_refs = []

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                inst = cls(result="ANSWER")
                inst._tools_enabled = tools_enabled
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return self.result

            async def cleanup_runtime(self):
                self.cleanup_called = True

        provider = create_provider(self.user, name="sub-provider")
        agent = create_agent(
            self.user,
            provider,
            name="DB-backed sub-agent",
            is_tool=True,
            tool_description="desc",
        )
        wrapper = self.AgentToolWrapper(
            agent_config=agent,
            thread=self.thread,
            user=self.user,
        )

        with patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent):
            with patch(
                "nova.tools.agent_tool_wrapper.invoke_native_provider_for_message",
                return_value=None,
            ):
                tool = wrapper.create_langchain_tool()
                answer, artifact_payload = asyncio.run(tool["coroutine"]("Hello from parent"))

        self.assertEqual(answer, "ANSWER")
        self.assertEqual(artifact_payload, {})
        self.assertTrue(FakeLLMAgent.instances[-1].cleanup_called)
        self.assertTrue(FakeLLMAgent.instances[-1].invoke_calls)
