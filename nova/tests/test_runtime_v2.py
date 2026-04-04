from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from nova.message_submission import SubmissionContext, submit_user_message
from nova.models.AgentConfig import AgentConfig
from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.Message import Actor
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Thread import Thread
from nova.runtime_v2.agent import ReactTerminalRuntime
from nova.runtime_v2.capabilities import TerminalCapabilities
from nova.runtime_v2.support import get_v2_runtime_error
from nova.runtime_v2.terminal import TerminalExecutor
from nova.runtime_v2.vfs import VirtualFileSystem


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


class ReactTerminalRuntimeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="runtime-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
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

        answer = async_to_sync(runtime.run)()

        session = AgentThreadSession.objects.get(
            thread=self.thread,
            agent_config=self.agent,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        self.assertEqual(answer, "The current directory is /workspace.")
        self.assertIn("pwd", session.session_state["history"])


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
