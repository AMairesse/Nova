from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase

from nova.message_submission import SubmissionContext, submit_user_message
from nova.models.AgentConfig import AgentConfig
from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.Memory import MemoryItem, MemoryItemStatus, MemoryTheme
from nova.models.Message import Actor
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserFile import UserFile
from nova.runtime_v2.agent import ReactTerminalRunResult, ReactTerminalRuntime
from nova.runtime_v2.capabilities import TerminalCapabilities
from nova.runtime_v2.compaction import (
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from nova.runtime_v2.skills_registry import build_skill_registry
from nova.runtime_v2.support import get_v2_runtime_error
from nova.runtime_v2.task_executor import (
    ReactTerminalSummarizationTaskExecutor,
    ReactTerminalTaskExecutor,
)
from nova.runtime_v2.terminal import TerminalCommandError, TerminalExecutor
from nova.runtime_v2.vfs import VirtualFileSystem
from nova.tasks.TaskProgressHandler import TaskProgressHandler
from nova.thread_titles import build_default_thread_subject


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

    def test_get_v2_runtime_error_accepts_continuous_mode(self):
        error = get_v2_runtime_error(
            self.agent,
            thread_mode=Thread.Mode.CONTINUOUS,
        )

        self.assertIsNone(error)


class TerminalExecutorTests(TestCase):
    def test_terminal_can_list_skills_and_change_directory(self):
        vfs = VirtualFileSystem(
            thread=SimpleNamespace(id=1),
            user=SimpleNamespace(id=1),
            agent_config=SimpleNamespace(id=42),
            session_state={"cwd": "/", "history": [], "directories": ["/tmp"]},
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


class TerminalExecutorCommandTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="terminal-user", password="pwd")
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4.1-mini",
            api_key="test-key",
        )
        self.agent = AgentConfig.objects.create(
            user=self.user,
            name="Terminal Command Agent",
            llm_provider=self.provider,
            system_prompt="",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        self.thread = Thread.objects.create(user=self.user, subject="Terminal thread")
        self.base_state = {
            "cwd": "/",
            "history": [],
            "directories": ["/tmp"],
        }
        self._stored_contents: dict[str, bytes] = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        self.upload_patcher = patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio)
        self.vfs_upload_patcher = patch("nova.runtime_v2.vfs.upload_file_to_minio", new=fake_upload_file_to_minio)
        self.download_patcher = patch("nova.runtime_v2.vfs.download_file_content", new=fake_download_file_content)
        self.delete_storage_patcher = patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock())
        self.upload_patcher.start()
        self.vfs_upload_patcher.start()
        self.download_patcher.start()
        self.delete_storage_patcher.start()
        self.addCleanup(self.upload_patcher.stop)
        self.addCleanup(self.vfs_upload_patcher.stop)
        self.addCleanup(self.download_patcher.stop)
        self.addCleanup(self.delete_storage_patcher.stop)

    def _build_executor(self, capabilities: TerminalCapabilities | None = None):
        resolved_capabilities = capabilities or TerminalCapabilities()
        vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state=dict(self.base_state),
            skill_registry={},
            memory_enabled=resolved_capabilities.has_memory,
        )
        return TerminalExecutor(vfs=vfs, capabilities=resolved_capabilities)

    def _build_executor_for_thread(
        self,
        thread,
        capabilities: TerminalCapabilities | None = None,
    ):
        resolved_capabilities = capabilities or TerminalCapabilities()
        vfs = VirtualFileSystem(
            thread=thread,
            user=self.user,
            agent_config=self.agent,
            session_state=dict(self.base_state),
            skill_registry={},
            memory_enabled=resolved_capabilities.has_memory,
        )
        return TerminalExecutor(vfs=vfs, capabilities=resolved_capabilities)

    def _create_builtin_tool(self, subtype: str, *, name: str, description: str = "") -> Tool:
        python_path_map = {
            "email": "nova.tools.builtins.email",
            "code_execution": "nova.tools.builtins.code_execution",
            "date": "nova.tools.builtins.date",
            "browser": "nova.tools.builtins.browser",
            "memory": "nova.tools.builtins.memory",
        }
        return Tool.objects.create(
            user=self.user,
            name=name,
            description=description or name,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype=subtype,
            python_path=python_path_map.get(subtype, ""),
        )

    def _create_email_tool(
        self,
        *,
        name: str,
        address: str,
        imap_server: str = "imap.example.com",
        smtp_server: str = "smtp.example.com",
        enable_sending: bool = True,
    ) -> Tool:
        tool = self._create_builtin_tool("email", name=name)
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "imap_server": imap_server,
                "smtp_server": smtp_server,
                "username": address,
                "password": "secret",
                "from_address": address,
                "enable_sending": enable_sending,
            },
        )
        return tool

    def _create_code_execution_tool(self) -> Tool:
        tool = self._create_builtin_tool("code_execution", name="Judge0")
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={"judge0_url": "https://judge0.example.com", "timeout": 5},
        )
        return tool

    def _create_memory_tool(self) -> Tool:
        return self._create_builtin_tool("memory", name="Memory")

    def test_touch_and_tee_create_and_append_root_files(self):
        executor = self._build_executor()

        created = async_to_sync(executor.execute)("touch note.txt")
        written = async_to_sync(executor.execute)('tee note.txt --text "hello"')
        appended = async_to_sync(executor.execute)('tee note.txt --text " world" --append')
        content = async_to_sync(executor.execute)("cat note.txt")

        self.assertIn("Created empty file /note.txt", created)
        self.assertIn("Wrote 5 bytes to /note.txt", written)
        self.assertIn("Wrote 6 bytes to /note.txt", appended)
        self.assertEqual(content, "hello world")

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("touch /skills/blocked.txt")

    def test_root_listing_shows_root_files_skills_and_tmp_without_legacy_mounts(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)("touch /note.txt")
        listing = async_to_sync(executor.execute)("ls /")

        self.assertIn("skills/", listing)
        self.assertIn("tmp/", listing)
        self.assertIn("note.txt", listing)
        self.assertNotIn("workspace/", listing)
        self.assertNotIn("thread/", listing)

    def test_memory_mount_is_visible_only_when_memory_capability_is_enabled(self):
        plain_executor = self._build_executor()
        memory_executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        plain_listing = async_to_sync(plain_executor.execute)("ls /")
        memory_listing = async_to_sync(memory_executor.execute)("ls /")

        self.assertNotIn("memory/", plain_listing)
        self.assertIn("memory/", memory_listing)

    def test_memory_paths_are_reserved_without_memory_capability(self):
        executor = self._build_executor()

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mkdir /memory/preferences")
        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)('tee /memory/preferences/editor.md --text "Vim"')

    def test_memory_mount_supports_ls_cat_and_grep(self):
        theme = MemoryTheme.objects.create(user=self.user, slug="preferences", display_name="Preferences")
        item = MemoryItem.objects.create(
            user=self.user,
            theme=theme,
            type="preference",
            content="Preferred editor is Vim",
            virtual_path="/memory/preferences/editor.md",
        )
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        memory_root = async_to_sync(executor.execute)("ls /memory")
        memory_theme = async_to_sync(executor.execute)("ls /memory/preferences")
        memory_doc = async_to_sync(executor.execute)("cat /memory/preferences/editor.md")
        grep_result = async_to_sync(executor.execute)('grep -r -n "Vim" /memory')

        self.assertIn("README.md", memory_root)
        self.assertIn("preferences/", memory_root)
        self.assertIn("editor.md", memory_theme)
        self.assertIn("Preferred editor is Vim", memory_doc)
        self.assertIn("/memory/preferences/editor.md", grep_result)
        self.assertIn("type: preference", memory_doc)
        self.assertEqual(item.id, MemoryItem.objects.get(id=item.id).id)

    def test_tee_and_rm_manage_memory_items(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        written = async_to_sync(executor.execute)(
            'tee /memory/preferences/editor.md --text "---\\ntype: preference\\n---\\nVim"'
        )
        content = async_to_sync(executor.execute)("cat /memory/preferences/editor.md")
        removed = async_to_sync(executor.execute)("rm /memory/preferences/editor.md")

        item = MemoryItem.objects.get(user=self.user, virtual_path="/memory/preferences/editor.md")
        self.assertIn("/memory/preferences/editor.md", written)
        self.assertIn("Vim", content)
        self.assertEqual(removed, "Removed /memory/preferences/editor.md")
        self.assertEqual(item.status, MemoryItemStatus.ARCHIVED)

    def test_touch_and_mv_manage_memory_items(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        async_to_sync(executor.execute)("mkdir /memory/preferences")
        created = async_to_sync(executor.execute)("touch /memory/preferences/editor.md")
        moved = async_to_sync(executor.execute)("mv /memory/preferences/editor.md /memory/tools/editor.txt")
        content = async_to_sync(executor.execute)("cat /memory/tools/editor.txt")

        item = MemoryItem.objects.get(user=self.user, virtual_path="/memory/tools/editor.txt")
        self.assertEqual(created, "Created empty file /memory/preferences/editor.md")
        self.assertEqual(moved, "Moved to /memory/tools/editor.txt")
        self.assertIn("path: /memory/tools/editor.txt", content)
        self.assertEqual(item.theme.slug, "tools")

    def test_memory_rejects_paths_deeper_than_one_theme_directory(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)('tee /memory/preferences/editors/vim.md --text "Vim"')

    def test_memory_search_formats_results_with_paths(self):
        executor = self._build_executor(
            TerminalCapabilities(memory_tool=object())
        )

        with patch(
            "nova.runtime_v2.terminal.search_memory_items",
            new_callable=AsyncMock,
            return_value={
                "results": [
                    {
                        "id": 7,
                        "path": "/memory/preferences/editor.md",
                        "theme": "preferences",
                        "type": "preference",
                        "content_snippet": "Uses Vim",
                    }
                ],
                "notes": [],
            },
        ) as mocked_search:
            result = async_to_sync(executor.execute)(
                'memory search "editor preference" --limit 2 --theme preferences --type preference'
            )

        self.assertIn("/memory/preferences/editor.md", result)
        self.assertIn("Uses Vim", result)
        self.assertEqual(mocked_search.await_args.kwargs["query"], "editor preference")
        self.assertEqual(mocked_search.await_args.kwargs["theme"], "preferences")
        self.assertEqual(mocked_search.await_args.kwargs["types"], ["preference"])

    def test_copy_and_move_preserve_source_basename_for_directory_destinations(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('tee /a.txt --text "hello"')
        async_to_sync(executor.execute)("mkdir /docs")

        moved = async_to_sync(executor.execute)("mv /a.txt /docs")
        copied = async_to_sync(executor.execute)("cp /docs/a.txt /")

        self.assertEqual(moved, "Moved to /docs/a.txt")
        self.assertEqual(copied, "Copied to /a.txt")
        self.assertEqual(async_to_sync(executor.execute)("cat /docs/a.txt"), "hello")
        self.assertEqual(async_to_sync(executor.execute)("cat /a.txt"), "hello")
        self.assertNotIn("\n\n", async_to_sync(executor.execute)("ls /docs"))

    def test_date_command_supports_native_formats(self):
        executor = self._build_executor(
            TerminalCapabilities(date_time_tool=object())
        )

        default_output = async_to_sync(executor.execute)("date")
        utc_output = async_to_sync(executor.execute)("date -u")
        date_only = async_to_sync(executor.execute)("date +%F")
        time_only = async_to_sync(executor.execute)("date +%T")

        self.assertRegex(default_output, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+$")
        self.assertRegex(utc_output, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$")
        self.assertRegex(date_only, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(time_only, r"^\d{2}:\d{2}:\d{2}$")

    def test_history_commands_are_available_in_continuous_mode(self):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous thread",
            mode=Thread.Mode.CONTINUOUS,
        )
        executor = self._build_executor_for_thread(continuous_thread)

        with patch(
            "nova.runtime_v2.terminal.conversation_search",
            new_callable=AsyncMock,
            return_value={
                "results": [
                    {
                        "kind": "message",
                        "day_label": "2026-04-04",
                        "day_segment_id": 5,
                        "message_id": 42,
                        "snippet": "important note",
                    }
                ],
                "notes": [],
            },
        ) as mocked_search:
            search_result = async_to_sync(executor.execute)(
                'history search "important note" --limit 2'
            )

        self.assertIn("message_id=42", search_result)
        self.assertIn("important note", search_result)
        self.assertEqual(mocked_search.await_args.kwargs["query"], "important note")
        self.assertEqual(mocked_search.await_args.kwargs["limit"], 2)
        self.assertEqual(mocked_search.await_args.kwargs["agent"].thread, continuous_thread)

        with patch(
            "nova.runtime_v2.terminal.conversation_get",
            new_callable=AsyncMock,
            return_value={
                "messages": [
                    {
                        "message_id": 42,
                        "role": Actor.USER,
                        "content": "important note",
                        "created_at": "2026-04-04T10:00:00+00:00",
                    }
                ],
                "truncated": False,
            },
        ) as mocked_get:
            get_result = async_to_sync(executor.execute)(
                "history get --message 42 --limit 5"
            )

        self.assertIn("[42]", get_result)
        self.assertIn("important note", get_result)
        self.assertEqual(mocked_get.await_args.kwargs["message_id"], 42)
        self.assertEqual(mocked_get.await_args.kwargs["limit"], 5)

    def test_mail_accounts_and_multi_mailbox_selection_are_explicit(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        personal_tool = self._create_email_tool(name="Personal Mail", address="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool, personal_tool])
        )

        accounts = async_to_sync(executor.execute)("mail accounts")
        self.assertIn("work@example.com", accounts)
        self.assertIn("personal@example.com", accounts)

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mail list")

        with patch("nova.tools.builtins.email.list_emails", new_callable=AsyncMock, return_value="ok") as mocked_list:
            listed = async_to_sync(executor.execute)("mail list --mailbox personal@example.com --limit 5")

        self.assertEqual(listed, "ok")
        mocked_list.assert_awaited_once_with(self.user, personal_tool.id, folder="INBOX", limit=5)

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("mail list --mailbox missing@example.com")

    def test_mail_rejects_ambiguous_mailbox_identifiers(self):
        first_tool = self._create_email_tool(
            name="Shared Mail A",
            address="shared@example.com",
            imap_server="imap.example.com",
        )
        second_tool = self._create_email_tool(
            name="Shared Mail B",
            address="shared@example.com",
            imap_server="imap.example.com",
        )
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[first_tool, second_tool])
        )

        with self.assertRaises(TerminalCommandError) as cm:
            async_to_sync(executor.execute)("mail list --mailbox shared@example.com")

        self.assertIn("Ambiguous mailbox", str(cm.exception))

    def test_single_mailbox_allows_mail_commands_without_mailbox_flag(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool])
        )

        with patch("nova.tools.builtins.email.list_emails", new_callable=AsyncMock, return_value="ok") as mocked_list:
            listed = async_to_sync(executor.execute)("mail list --limit 3")

        self.assertEqual(listed, "ok")
        mocked_list.assert_awaited_once_with(self.user, work_tool.id, folder="INBOX", limit=3)

    def test_mail_send_uses_selected_mailbox(self):
        work_tool = self._create_email_tool(name="Work Mail", address="work@example.com")
        personal_tool = self._create_email_tool(name="Personal Mail", address="personal@example.com")
        executor = self._build_executor(
            TerminalCapabilities(email_tools=[work_tool, personal_tool])
        )
        async_to_sync(executor.vfs.write_file)(
            "/body.txt",
            b"Hello from Nova",
            mime_type="text/plain",
        )

        with patch.object(executor, "_send_mail_direct", new=AsyncMock(return_value="sent")) as mocked_send:
            result = async_to_sync(executor.execute)(
                "mail send --mailbox personal@example.com --to bob@example.com "
                '--subject "Hello" --body-file /body.txt'
            )

        self.assertEqual(result, "sent")
        mocked_send.assert_awaited_once()
        self.assertEqual(mocked_send.await_args.kwargs["tool_id"], personal_tool.id)

    def test_python_output_writes_stdout_file_and_preserves_terminal_result(self):
        code_tool = self._create_code_execution_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool)
        )
        async_to_sync(executor.execute)(
            'tee /script.py --text "print(\'hello\')\nprint(\'world\')"'
        )
        async_to_sync(executor.execute)("mkdir /results")

        with (
            patch(
                "nova.tools.builtins.code_execution.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.tools.builtins.code_execution.execute_code",
                new_callable=AsyncMock,
                return_value="Status: Accepted\nStdout: hello\nworld\nStderr: ",
            ) as mocked_execute,
        ):
            result = async_to_sync(executor.execute)(
                "python --output /results /script.py"
            )

        output_file = async_to_sync(executor.execute)("cat /results/script.stdout.txt")
        self.assertIn("Status: Accepted", result)
        self.assertEqual(output_file, "hello\nworld")
        self.assertEqual(mocked_execute.await_args.args[1], "print('hello')\nprint('world')")

    def test_skill_registry_mentions_mail_python_and_date_guidance(self):
        skills = build_skill_registry(
            TerminalCapabilities(
                email_tools=[object(), object()],
                code_execution_tool=object(),
                date_time_tool=object(),
            )
        )

        self.assertIn("mail.md", skills)
        self.assertIn("python.md", skills)
        self.assertIn("date.md", skills)
        self.assertIn("mail accounts", skills["mail.md"])
        self.assertIn("--mailbox <email>", skills["mail.md"])
        self.assertIn("python --output", skills["python.md"])
        self.assertIn("date +%F", skills["date.md"])

    def test_skill_registry_adds_memory_guide_when_memory_is_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(memory_tool=object())
        )

        self.assertIn("memory.md", skills)
        self.assertIn("memory search", skills["memory.md"])
        self.assertIn("grep", skills["memory.md"])

    def test_skill_registry_adds_continuous_guide_in_continuous_mode(self):
        skills = build_skill_registry(
            TerminalCapabilities(),
            thread_mode=Thread.Mode.CONTINUOUS,
        )

        self.assertIn("continuous.md", skills)
        self.assertIn("history search", skills["continuous.md"])
        self.assertIn("history get", skills["continuous.md"])


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
                    "content": "The current directory is /.",
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
        self.assertEqual(result.final_answer, "The current directory is /.")
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
            await on_content_delta(" directory is /.")
            return {
                "content": "The current directory is /.",
                "tool_calls": [],
                "total_tokens": 123,
                "streamed": True,
            }

        runtime.provider_client.stream_chat_completion = AsyncMock(side_effect=fake_stream_chat_completion)

        result = async_to_sync(runtime.run)()

        task.refresh_from_db()
        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertEqual(result.final_answer, "The current directory is /.")
        self.assertEqual(result.real_tokens, 123)
        self.assertIn("response_chunk", event_types)
        self.assertIn("progress_update", event_types)
        self.assertIn("The current directory is /.", task.streamed_markdown)
        self.assertIsNotNone(task.current_response)

    def test_runtime_loads_compacted_history_summary(self):
        first = self.thread.add_message("Initial requirement", Actor.USER)
        self.thread.add_message("Recent context", Actor.USER)
        session = AgentThreadSession.objects.create(
            thread=self.thread,
            agent_config=self.agent,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            session_state={
                "cwd": "/",
                "history": [],
                "directories": ["/tmp"],
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

    def test_runtime_loads_continuous_context_and_rewrites_history_guidance(self):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous runtime",
            mode=Thread.Mode.CONTINUOUS,
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=continuous_thread,
                agent_config=self.agent,
            ).initialize
        )()

        fake_messages = [
            SimpleNamespace(
                type="system",
                content="Use conversation_search and conversation_get for older context.",
            ),
            SimpleNamespace(type="human", content="User note"),
            SimpleNamespace(type="ai", content="Assistant note"),
        ]

        with patch(
            "nova.runtime_v2.agent.load_continuous_context",
            return_value=(SimpleNamespace(), fake_messages),
        ) as mocked_loader:
            history = async_to_sync(runtime._load_history_messages)()

        self.assertEqual(history[0]["role"], "system")
        self.assertIn("history search", history[0]["content"])
        self.assertIn("history get", history[0]["content"])
        self.assertEqual(history[1], {"role": "user", "content": "User note"})
        self.assertEqual(history[2], {"role": "assistant", "content": "Assistant note"})
        mocked_loader.assert_called_once_with(
            self.user,
            continuous_thread,
            exclude_message_id=None,
        )

    def test_system_prompt_mentions_touch_tee_and_conditional_mailbox_and_date_guidance(self):
        date_tool = Tool.objects.create(
            user=self.user,
            name="Date",
            description="Date",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="date",
            python_path="nova.tools.builtins.date",
        )
        first_mail = Tool.objects.create(
            user=self.user,
            name="Work Mail",
            description="Work Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        second_mail = Tool.objects.create(
            user=self.user,
            name="Personal Mail",
            description="Personal Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=first_mail,
            config={
                "imap_server": "imap.work.example.com",
                "username": "work@example.com",
                "password": "secret",
                "from_address": "work@example.com",
            },
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=second_mail,
            config={
                "imap_server": "imap.personal.example.com",
                "username": "personal@example.com",
                "password": "secret",
                "from_address": "personal@example.com",
            },
        )
        self.agent.tools.add(date_tool, first_mail, second_mail)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("touch", prompt)
        self.assertIn("tee", prompt)
        self.assertIn("date +%F", prompt)
        self.assertIn("--mailbox <email>", prompt)
        self.assertIn("- /: persistent files for this thread", prompt)
        self.assertNotIn("/thread", prompt)
        self.assertNotIn("/workspace", prompt)

    def test_system_prompt_mentions_memory_mount_and_search_guidance(self):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(memory_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("/memory", prompt)
        self.assertIn("grep", prompt)
        self.assertIn("memory search", prompt)

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_memory_is_shared_between_threads_for_same_user(self, mocked_provider):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(memory_tool)
        other_thread = Thread.objects.create(user=self.user, subject="Other thread")
        other_thread.add_message("Check memory", Actor.USER)

        runtime_a = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                source_message_id=self.thread.get_messages().order_by("id").first().id,
            ).initialize
        )()
        runtime_b = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=other_thread,
                agent_config=self.agent,
                source_message_id=other_thread.get_messages().order_by("id").first().id,
            ).initialize
        )()

        async_to_sync(runtime_a.vfs.write_file)(
            "/memory/preferences/editor.md",
            b"---\ntype: preference\n---\nUses Vim",
            mime_type="text/markdown",
        )
        content = async_to_sync(runtime_b.vfs.read_text)("/memory/preferences/editor.md")

        self.assertIn("Uses Vim", content)
        mocked_provider.assert_awaited()

    def test_continuous_system_prompt_mentions_history_commands(self):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous prompt thread",
            mode=Thread.Mode.CONTINUOUS,
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=continuous_thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()

        self.assertIn("continuous thread", prompt)
        self.assertIn("history search", prompt)
        self.assertIn("history get", prompt)

    def test_subagent_outputs_are_copied_back_under_subagents_directory(self):
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Child Agent",
            llm_provider=self.provider,
            system_prompt="Child",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        self._stored_contents: dict[str, bytes] = {}
        seen = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ensure_root_trace
            seen["prompt"] = ephemeral_user_prompt
            await self.vfs.write_file("/answer.txt", b"answer", mime_type="text/plain")
            await self.vfs.write_file("/tmp/ignored.txt", b"temp", mime_type="text/plain")
            return ReactTerminalRunResult(
                final_answer="Child done.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime_v2.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime_v2.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch("nova.runtime_v2.agent.ReactTerminalRuntime.run", new=fake_child_run),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()
            async_to_sync(runtime.vfs.write_file)("/input.txt", b"parent", mime_type="text/plain")

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Use the input.",
                input_paths=["/input.txt"],
            )

            copied_paths = async_to_sync(runtime.vfs.find)("/subagents", "")
            copied_answer_path = next(path for path in copied_paths if path.endswith("/answer.txt"))
            copied_answer = async_to_sync(runtime.vfs.read_text)(copied_answer_path)

        self.assertIn("Child done.", result)
        self.assertIn("/inbox/input.txt", seen["prompt"])
        self.assertTrue(any(path.endswith("/answer.txt") for path in copied_paths))
        self.assertFalse(any(path.endswith("/ignored.txt") for path in copied_paths))
        self.assertEqual(copied_answer, "answer")

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_subagent_with_memory_capability_shares_memory_mount(self, mocked_provider):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        self.agent.tools.add(memory_tool)

        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Memory Child",
            llm_provider=self.provider,
            system_prompt="Child",
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
            recursion_limit=2,
            is_tool=True,
            tool_description="Child memory tool",
        )
        child_agent.tools.add(memory_tool)
        self.agent.agent_tools.add(child_agent)

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ephemeral_user_prompt, ensure_root_trace
            await self.vfs.write_file(
                "/memory/preferences/editor.md",
                b"---\ntype: preference\n---\nUses Vim",
                mime_type="text/markdown",
            )
            return ReactTerminalRunResult(
                final_answer="Stored memory.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with patch("nova.runtime_v2.agent.ReactTerminalRuntime.run", new=fake_child_run):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Remember the editor.",
                input_paths=[],
            )

        content = async_to_sync(runtime.vfs.read_text)("/memory/preferences/editor.md")
        self.assertIn("Stored memory.", result)
        self.assertIn("Uses Vim", content)
        mocked_provider.assert_awaited()


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

    def test_task_executor_enqueues_thread_title_generation_for_default_subject(self):
        thread = Thread.objects.create(user=self.user, subject=build_default_thread_subject(1))
        source_message = thread.add_message("Give me the result.", Actor.USER)
        task = Task.objects.create(
            user=self.user,
            thread=thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()

        async def fake_stream_chat_completion(self, *, messages, tools, on_content_delta):
            del self, messages, tools
            await on_content_delta("Result")
            return {
                "content": "Result",
                "tool_calls": [],
                "total_tokens": 5,
                "streamed": True,
            }

        with (
            patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=channel_layer),
            patch(
                "nova.runtime_v2.provider_client.OpenAICompatibleProviderClient.stream_chat_completion",
                new=fake_stream_chat_completion,
            ),
            patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay,
        ):
            executor = ReactTerminalTaskExecutor(
                task,
                self.user,
                thread,
                self.agent,
                source_message.text,
                source_message_id=source_message.id,
                push_notifications_enabled=False,
            )
            async_to_sync(executor.execute_or_resume)()

        mocked_delay.assert_called_once_with(
            thread_id=thread.id,
            user_id=self.user.id,
            agent_config_id=self.agent.id,
            source_task_id=task.id,
        )

    def test_task_executor_skips_thread_title_generation_for_custom_subject(self):
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()

        async def fake_stream_chat_completion(self, *, messages, tools, on_content_delta):
            del self, messages, tools
            await on_content_delta("Result")
            return {
                "content": "Result",
                "tool_calls": [],
                "total_tokens": 5,
                "streamed": True,
            }

        with (
            patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=channel_layer),
            patch(
                "nova.runtime_v2.provider_client.OpenAICompatibleProviderClient.stream_chat_completion",
                new=fake_stream_chat_completion,
            ),
            patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay,
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

        mocked_delay.assert_not_called()

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

    def test_summarization_executor_rejects_continuous_mode(self):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous compaction",
            mode=Thread.Mode.CONTINUOUS,
        )
        continuous_thread.add_message("Message 1", Actor.USER)
        continuous_thread.add_message("Message 2", Actor.AGENT)
        continuous_thread.add_message("Message 3", Actor.USER)
        task = Task.objects.create(
            user=self.user,
            thread=continuous_thread,
            agent_config=self.agent,
        )
        channel_layer = _FakeChannelLayer()

        with patch("nova.tasks.TaskExecutor.get_channel_layer", return_value=channel_layer):
            executor = ReactTerminalSummarizationTaskExecutor(
                task,
                self.user,
                continuous_thread,
                self.agent,
            )
            async_to_sync(executor.execute)()

        task.refresh_from_db()
        event_types = [item["message"]["type"] for item in channel_layer.messages]
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("task_error", event_types)
        self.assertIn("continuous mode", task.result)


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
