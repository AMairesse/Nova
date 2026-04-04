from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase

from nova.message_submission import SubmissionContext, submit_user_message
from nova.models.AgentConfig import AgentConfig
from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.Message import Actor
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.runtime_v2.agent import ReactTerminalRuntime
from nova.runtime_v2.capabilities import TerminalCapabilities
from nova.runtime_v2.compaction import (
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from nova.runtime_v2.support import get_v2_runtime_error
from nova.runtime_v2.task_executor import (
    ReactTerminalSummarizationTaskExecutor,
    ReactTerminalTaskExecutor,
)
from nova.runtime_v2.terminal import TerminalExecutor
from nova.runtime_v2.vfs import VirtualFileSystem
from nova.tasks.TaskProgressHandler import TaskProgressHandler


class _FakeChannelLayer:
    def __init__(self):
        self.messages = []

    async def group_send(self, group_name, payload):
        self.messages.append({"group": group_name, "message": payload["message"]})


class RuntimeV2SupportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="v2-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="Terminal Agent",
            llm_provider=self.provider,
            system_prompt="",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )

    def test_get_v2_runtime_error_rejects_continuous_mode(self):
        error = get_v2_runtime_error(
            self.agent,
            thread_mode=Thread.Mode.CONTINUOUS,
        )

        self.assertEqual(
            error,
            "React Terminal V1 only supports standard thread mode.",
        )


class TerminalExecutorTests(TestCase):
    def test_terminal_can_list_skills_and_change_directory(self):
        vfs = VirtualFileSystem(
            thread=SimpleNamespace(id=1),
            user=SimpleNamespace(id=1),
            agent_config=SimpleNamespace(id=42),
            session_state={"cwd": "/workspace", "history": [], "directories": ["/workspace", "/tmp"]},
            skill_registry={"mail.md": "# Mail\n", "python.md": "# Python\n"},
        )
        executor = TerminalExecutor(vfs=vfs, capabilities=TerminalCapabilities())

        skills_listing = async_to_sync(executor.execute)("ls /skills")
        cwd = async_to_sync(executor.execute)("cd /tmp")
        pwd = async_to_sync(executor.execute)("pwd")

        self.assertIn("mail.md", skills_listing)
        self.assertIn("python.md", skills_listing)
        self.assertEqual(cwd, "/tmp")
        self.assertEqual(pwd, "/tmp")


class ReactTerminalRuntimeTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="runtime-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
            max_context_tokens=8192,
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="Runtime Agent",
            llm_provider=self.provider,
            system_prompt="Be concise.",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            recursion_limit=4,
        )
        self.thread = Thread.objects.create(user=self.user, subject="Test thread")
        self.thread.add_message("Check the current directory.", Actor.USER)

    def test_runtime_executes_terminal_tool_loop(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        runtime.provider_client.create_chat_completion = AsyncMock(
            side_effect=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "terminal",
                            "arguments": '{"command":"pwd"}',
                        }
                    ],
                },
                {
                    "content": "The current directory is /workspace.",
                    "tool_calls": [],
                },
            ]
        )

        result = async_to_sync(runtime.run)()

        session = AgentThreadSession.objects.get(
            thread=self.thread,
            agent_config=self.agent,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        self.assertEqual(result.final_answer, "The current directory is /workspace.")
        self.assertIn("pwd", session.session_state["history"])

    def test_runtime_persists_stream_state_for_reconnect(self):
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()
        handler = TaskProgressHandler(
            task.id,
            channel_layer,
            user_id=self.user.id,
            thread_id=self.thread.id,
            thread_mode=self.thread.mode,
            push_notifications_enabled=False,
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                task=task,
                progress_handler=handler,
            ).initialize
        )()

        async def fake_stream_chat_completion(*, messages, tools, on_content_delta):
            del messages, tools
            await on_content_delta("The current")
            await on_content_delta(" directory is /workspace.")
            return {
                "content": "The current directory is /workspace.",
                "tool_calls": [],
                "total_tokens": 123,
                "streamed": True,
            }

        runtime.provider_client.stream_chat_completion = AsyncMock(side_effect=fake_stream_chat_completion)

        result = async_to_sync(runtime.run)()

        task.refresh_from_db()
        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertEqual(result.final_answer, "The current directory is /workspace.")
        self.assertEqual(result.real_tokens, 123)
        self.assertIn("response_chunk", event_types)
        self.assertIn("progress_update", event_types)
        self.assertIn("The current directory is /workspace.", task.streamed_markdown)
        self.assertIsNotNone(task.current_response)

    def test_runtime_loads_compacted_history_summary(self):
        first = self.thread.add_message("Initial requirement", Actor.USER)
        self.thread.add_message("Recent context", Actor.USER)
        session = AgentThreadSession.objects.create(
            thread=self.thread,
            agent_config=self.agent,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            session_state={
                "cwd": "/workspace",
                "history": [],
                "directories": ["/workspace", "/tmp"],
                SESSION_KEY_HISTORY_SUMMARY: "## Summary\nPrevious goals",
                SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID: first.id,
            },
        )

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        runtime.session = session
        history = async_to_sync(runtime._load_history_messages)()

        self.assertEqual(history[0]["role"], "system")
        self.assertIn("Previous goals", history[0]["content"])
        self.assertFalse(any(item["content"] == "Initial requirement" for item in history[1:]))
        self.assertTrue(any(item["content"] == "Recent context" for item in history[1:]))


class ReactTerminalExecutorTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="executor-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
            max_context_tokens=4096,
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="Executor Agent",
            llm_provider=self.provider,
            system_prompt="Be concise.",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            recursion_limit=4,
        )
        self.thread = Thread.objects.create(user=self.user, subject="Executor thread")
        self.source_message = self.thread.add_message("Give me the result.", Actor.USER)

    def test_task_executor_publishes_realtime_events_and_footer_metadata(self):
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()

        async def fake_stream_chat_completion(self, *, messages, tools, on_content_delta):
            del self, messages, tools
            await on_content_delta("Result")
            await on_content_delta(" ready.")
            return {
                "content": "Result ready.",
                "tool_calls": [],
                "total_tokens": 321,
                "streamed": True,
            }

        with (
            patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=channel_layer),
            patch(
                "nova.runtime_v2.provider_client.OpenAICompatibleProviderClient.stream_chat_completion",
                new=fake_stream_chat_completion,
            ),
        ):
            executor = ReactTerminalTaskExecutor(
                task,
                self.user,
                self.thread,
                self.agent,
                self.source_message.text,
                source_message_id=self.source_message.id,
                push_notifications_enabled=False,
            )
            async_to_sync(executor.execute_or_resume)()

        task.refresh_from_db()
        final_message = self.thread.get_messages().order_by("-id").first()
        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.current_response, None)
        self.assertEqual(task.streamed_markdown, "")
        self.assertEqual(final_message.actor, Actor.AGENT)
        self.assertEqual(final_message.internal_data["real_tokens"], 321)
        self.assertEqual(final_message.internal_data["max_context"], 4096)
        self.assertEqual(final_message.internal_data["trace_task_id"], task.id)
        self.assertTrue(final_message.internal_data["trace_summary"]["has_trace"])
        self.assertIn("response_chunk", event_types)
        self.assertIn("context_consumption", event_types)
        self.assertIn("new_message", event_types)
        self.assertIn("task_complete", event_types)

    def test_summarization_executor_updates_session_and_emits_completion(self):
        self.thread.add_message("Message 1", Actor.USER)
        self.thread.add_message("Message 2", Actor.AGENT)
        self.thread.add_message("Message 3", Actor.USER)
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()

        async def fake_create_chat_completion(self, *, messages, tools=None):
            del self, messages, tools
            return {"content": "## Summary\nKeep the recent context.", "tool_calls": [], "total_tokens": 77}

        with (
            patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=channel_layer),
            patch(
                "nova.runtime_v2.provider_client.OpenAICompatibleProviderClient.create_chat_completion",
                new=fake_create_chat_completion,
            ),
        ):
            executor = ReactTerminalSummarizationTaskExecutor(
                task,
                self.user,
                self.thread,
                self.agent,
            )
            async_to_sync(executor.execute)()

        task.refresh_from_db()
        session = AgentThreadSession.objects.get(
            thread=self.thread,
            agent_config=self.agent,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(session.session_state[SESSION_KEY_HISTORY_SUMMARY], "## Summary\nKeep the recent context.")
        self.assertIn(SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID, session.session_state)
        self.assertIn("summarization_complete", event_types)
        self.assertIn("task_complete", event_types)


class MessageSubmissionV2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="submit-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="Submission Agent",
            llm_provider=self.provider,
            system_prompt="",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        self.thread = Thread.objects.create(user=self.user, subject="Submission thread")

    def test_v2_message_attachments_are_merged_into_thread_files(self):
        uploaded = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        dispatcher_task = SimpleNamespace(delay=Mock())
        seen_file_data = {}

        async def fake_thread_file_uploader(thread, user, file_data):
            seen_file_data["value"] = list(file_data)
            return [{"id": 123}], []

        fake_attachment_uploader = Mock(side_effect=AssertionError("attachment_uploader should not be called"))
        fake_file_update_publisher = AsyncMock()

        def prepare_context(message_text: str) -> SubmissionContext:
            return SubmissionContext(
                thread=self.thread,
                create_message=lambda text: self.thread.add_message(text, Actor.USER),
            )

        result = submit_user_message(
            user=self.user,
            message_text="Here is the file.",
            selected_agent=str(self.agent.id),
            response_mode="text",
            thread_mode=Thread.Mode.THREAD,
            thread_files=[],
            message_attachments=[uploaded],
            prepare_context=prepare_context,
            dispatcher_task=dispatcher_task,
            thread_file_uploader=fake_thread_file_uploader,
            attachment_uploader=fake_attachment_uploader,
            file_update_publisher=fake_file_update_publisher,
        )

        self.assertEqual(result.uploaded_file_ids, [123])
        self.assertEqual(len(seen_file_data["value"]), 1)
        self.assertEqual(seen_file_data["value"][0]["path"], "/note.txt")
        fake_attachment_uploader.assert_not_called()
