from __future__ import annotations

import base64
import html
import ipaddress
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user
from nova.file_utils import build_message_attachment_path
from nova.message_submission import SubmissionContext, submit_user_message
from nova.exec_runner.service import SandboxShellResult
from nova.models.AgentConfig import AgentConfig
from nova.models.APIToolOperation import APIToolOperation
from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric
from nova.models.Message import Actor, MessageType
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserFile import UserFile
from nova.models.WebApp import WebApp
from nova.plugins.python import service as python_service
from nova.runtime.agent import (
    ReactTerminalInterruptResult,
    ReactTerminalRunResult,
    ReactTerminalRuntime,
)
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.compaction import (
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from nova.runtime.skills_registry import build_skill_registry
from nova.runtime.support import get_runtime_error
from nova.runtime.task_executor import (
    ReactTerminalSummarizationTaskExecutor,
    ReactTerminalTaskExecutor,
)
from nova.runtime.terminal import (
    TerminalCommandError,
    TerminalExecutor,
)
from nova.runtime.vfs import VirtualFileSystem
from nova.tasks.tasks import build_source_message_prompt
from nova.tasks.execution_trace import TaskExecutionTraceHandler
from nova.tasks.TaskProgressHandler import TaskProgressHandler
from nova.thread_titles import build_default_thread_subject
from nova.web.download_service import DEFAULT_DOWNLOAD_USER_AGENT, download_http_file
from nova.web.network_policy import NetworkPolicyError


class _FakeChannelLayer:
    def __init__(self):
        self.messages = []

    async def group_send(self, group_name, payload):
        self.messages.append({"group": group_name, "message": payload.get("message", payload)})


class _FakeBrowserSession:
    def __init__(self):
        self.open = AsyncMock(return_value={"url": "https://example.com", "status": 200})
        self.open_search_result = AsyncMock(return_value={"url": "https://example.com/result", "status": 200})
        self.current = AsyncMock(return_value="https://example.com/result")
        self.back = AsyncMock(return_value={"url": "https://example.com/previous", "status": 200})
        self.extract_text = AsyncMock(return_value="Page text " * 500)
        self.extract_links = AsyncMock(return_value=[{"href": "https://example.com/a", "text": "A"}])
        self.get_elements = AsyncMock(return_value=[{"href": "https://example.com/a", "innerText": "A"}])
        self.click = AsyncMock(return_value="Clicked element 'a.link'.")
        self.close = AsyncMock(return_value=None)


class _FakeDownloadStreamResponse:
    def __init__(
        self,
        *,
        headers: dict[str, str],
        chunks: list[bytes],
        status_code: int = 200,
        request_url: str = "https://example.com/file",
    ):
        self.headers = headers
        self._chunks = list(chunks)
        self.status_code = status_code
        self.request = SimpleNamespace(url=request_url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class RuntimeSupportTests(TestCase):
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
            name="Terminal Agent",
            llm_provider=self.provider,
            system_prompt="",
        )

    def test_get_runtime_error_accepts_continuous_mode(self):
        error = get_runtime_error(
            self.agent,
            thread_mode=Thread.Mode.CONTINUOUS,
        )

        self.assertIsNone(error)

    def test_runtime_initialize_handles_deferred_llm_provider_relation(self):
        thread = Thread.objects.create(user=self.user, subject="Deferred provider thread")
        deferred_agent = AgentConfig.objects.get(pk=self.agent.pk)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=thread,
                agent_config=deferred_agent,
            ).initialize
        )()

        self.assertEqual(runtime.provider_client.model, self.provider.model)
        self.assertEqual(runtime.provider_client.max_context_tokens, self.provider.max_context_tokens)


class DownloadServiceTests(SimpleTestCase):
    def test_download_http_file_sends_default_user_agent(self):
        captured = {}

        class FakeAsyncClient:
            def __init__(self, *, headers=None, **kwargs):
                captured["headers"] = dict(headers or {})
                captured["proxy"] = kwargs.get("proxy")
                captured["trust_env"] = kwargs.get("trust_env")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                captured["method"] = method
                captured["url"] = url
                return _FakeDownloadStreamResponse(
                    headers={"content-type": "text/plain"},
                    chunks=[b"hello"],
                )

        fake_proxy = AsyncMock()
        fake_proxy.proxy_url = "http://127.0.0.1:43123"
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient), patch(
            "nova.web.download_service.SafeHttpProxyServer",
            return_value=fake_proxy,
        ):
            payload = async_to_sync(download_http_file)("https://example.com/hello.txt")

        normalized_headers = {
            str(name).lower(): value for name, value in captured["headers"].items()
        }
        self.assertEqual(payload["content"], b"hello")
        self.assertEqual(payload["mime_type"], "text/plain")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "https://example.com/hello.txt")
        self.assertEqual(captured["proxy"], "http://127.0.0.1:43123")
        self.assertFalse(captured["trust_env"])
        self.assertEqual(normalized_headers["user-agent"], DEFAULT_DOWNLOAD_USER_AGENT)

    def test_download_http_file_allows_user_agent_override_and_custom_headers(self):
        captured = {}

        class FakeAsyncClient:
            def __init__(self, *, headers=None, **kwargs):
                captured["headers"] = dict(headers or {})
                captured["proxy"] = kwargs.get("proxy")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                del method, url
                return _FakeDownloadStreamResponse(
                    headers={"content-type": "text/plain"},
                    chunks=[b"ok"],
                )

        fake_proxy = AsyncMock()
        fake_proxy.proxy_url = "http://127.0.0.1:43123"
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient), patch(
            "nova.web.download_service.SafeHttpProxyServer",
            return_value=fake_proxy,
        ):
            payload = async_to_sync(download_http_file)(
                "https://example.com/data.txt",
                headers={"Referer": "https://example.com", "User-Agent": "HeaderUA/1.0"},
                user_agent="NovaOverride/2.0",
            )

        normalized_headers = {
            str(name).lower(): value for name, value in captured["headers"].items()
        }
        self.assertEqual(payload["content"], b"ok")
        self.assertEqual(captured["proxy"], "http://127.0.0.1:43123")
        self.assertEqual(normalized_headers["user-agent"], "NovaOverride/2.0")
        self.assertEqual(normalized_headers["referer"], "https://example.com")

    def test_download_http_file_enforces_max_size(self):
        class FakeAsyncClient:
            def __init__(self, **kwargs):
                del kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                del method, url
                return _FakeDownloadStreamResponse(
                    headers={"content-type": "image/png"},
                    chunks=[b"abc", b"def"],
                )

        fake_proxy = AsyncMock()
        fake_proxy.proxy_url = "http://127.0.0.1:43123"
        with patch(
            "nova.web.network_policy._resolve_host_addresses",
            return_value=(ipaddress.ip_address("93.184.216.34"),),
        ), patch("nova.web.download_service.httpx.AsyncClient", new=FakeAsyncClient), patch(
            "nova.web.download_service.SafeHttpProxyServer",
            return_value=fake_proxy,
        ):
            with self.assertRaisesMessage(ValueError, "exceeds"):
                async_to_sync(download_http_file)("https://example.com/image.png", max_size=5)


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
        self.vfs_upload_patcher = patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio)
        self.download_patcher = patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content)
        self.webapp_download_patcher = patch("nova.webapp.service.download_file_content", new=fake_download_file_content)
        self.delete_storage_patcher = patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock())
        self.upload_patcher.start()
        self.vfs_upload_patcher.start()
        self.download_patcher.start()
        self.webapp_download_patcher.start()
        self.delete_storage_patcher.start()
        self.addCleanup(self.upload_patcher.stop)
        self.addCleanup(self.vfs_upload_patcher.stop)
        self.addCleanup(self.download_patcher.stop)
        self.addCleanup(self.webapp_download_patcher.stop)
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
            webdav_tools=resolved_capabilities.webdav_tools,
        )
        return TerminalExecutor(vfs=vfs, capabilities=resolved_capabilities)

    def test_vfs_read_bytes_supports_skill_files(self):
        vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state=dict(self.base_state),
            skill_registry={"calendar.md": "# Calendar\n\nUse `calendar accounts`.\n"},
        )

        content, mime_type = async_to_sync(vfs.read_bytes)("/skills/calendar.md")

        self.assertEqual(content, b"# Calendar\n\nUse `calendar accounts`.\n")
        self.assertEqual(mime_type, "text/markdown")

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
            webdav_tools=resolved_capabilities.webdav_tools,
        )
        return TerminalExecutor(vfs=vfs, capabilities=resolved_capabilities)

    def test_vfs_write_file_persists_source_message_for_thread_files(self):
        source_message = self.thread.add_message("Source", actor=Actor.USER)
        vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state=dict(self.base_state),
            skill_registry={},
            source_message_id=source_message.id,
        )

        async_to_sync(vfs.write_file)("/report.txt", b"", mime_type="text/plain")

        user_file = UserFile.objects.get(
            user=self.user,
            thread=self.thread,
            original_filename="/report.txt",
        )
        self.assertEqual(user_file.source_message_id, source_message.id)

    def _create_builtin_tool(self, subtype: str, *, name: str, description: str = "") -> Tool:
        python_path_map = {
            "email": "nova.plugins.mail",
            "code_execution": "nova.plugins.python",
            "date": "nova.plugins.datetime",
            "browser": "nova.plugins.browser",
            "memory": "nova.plugins.memory",
            "searxng": "nova.plugins.search",
            "webdav": "nova.plugins.webdav",
            "webapp": "nova.plugins.webapp",
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

    def _create_webapp_tool(self) -> Tool:
        return self._create_builtin_tool("webapp", name="WebApp")

    def _create_mcp_tool(self, *, name: str = "Notion MCP", endpoint: str = "https://mcp.example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description=name,
            tool_type=Tool.ToolType.MCP,
            endpoint=endpoint,
            transport_type=Tool.TransportType.STREAMABLE_HTTP,
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type="token",
            token="mcp-token",
        )
        return tool

    def _create_api_tool(self, *, name: str = "CRM API", endpoint: str = "https://api.example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description=name,
            tool_type=Tool.ToolType.API,
            endpoint=endpoint,
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type="api_key",
            token="secret-api-key",
            config={"api_key_name": "X-API-Key", "api_key_in": "header"},
        )
        return tool

    def _create_api_operation(
        self,
        tool: Tool,
        *,
        name: str = "Create invoice",
        slug: str = "create-invoice",
        http_method: str = APIToolOperation.HTTPMethod.POST,
        path_template: str = "/invoices/{invoice_id}",
        query_parameters: list[str] | None = None,
        body_parameter: str = "",
        input_schema: dict | None = None,
        output_schema: dict | None = None,
    ) -> APIToolOperation:
        return APIToolOperation.objects.create(
            tool=tool,
            name=name,
            slug=slug,
            description=name,
            http_method=http_method,
            path_template=path_template,
            query_parameters=list(query_parameters or ["mode"]),
            body_parameter=body_parameter,
            input_schema=input_schema if input_schema is not None else {},
            output_schema=output_schema if output_schema is not None else {},
        )

    def _create_caldav_tool(self, *, name: str = "Work Calendar", username: str = "work@example.com") -> Tool:
        tool = self._create_builtin_tool("caldav", name=name)
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def _create_webdav_tool(
        self,
        *,
        name: str = "Nextcloud Docs",
        root_path: str = "/Documents",
        allow_create_files: bool = True,
        allow_create_directories: bool = True,
        allow_move: bool = True,
        allow_copy: bool = True,
        allow_delete: bool = True,
    ) -> Tool:
        tool = self._create_builtin_tool("webdav", name=name)
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "server_url": "https://cloud.example.com/remote.php/dav/files/alice",
                "username": "alice",
                "app_password": "secret",
                "root_path": root_path,
                "allow_create_files": allow_create_files,
                "allow_create_directories": allow_create_directories,
                "allow_move": allow_move,
                "allow_copy": allow_copy,
                "allow_delete": allow_delete,
            },
        )
        return tool

    def _create_caldav_tool(self, *, name: str = "Work Calendar", username: str = "work@example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description="CalDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def _create_caldav_tool(self, *, name: str = "Work Calendar", username: str = "work@example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description="CalDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def _create_caldav_tool(self, *, name: str = "Work Calendar", username: str = "work@example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description="CalDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def _create_browser_tool(self) -> Tool:
        return Tool.objects.create(
            user=self.user,
            name="Browser",
            description="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.plugins.browser",
        )

    def _create_webapp_tool(self) -> Tool:
        return Tool.objects.create(
            user=self.user,
            name="WebApp",
            description="WebApp",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="webapp",
            python_path="nova.plugins.webapp",
        )

    def _create_searxng_tool(self) -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name="SearXNG",
            description="Search",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.plugins.search",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={"searxng_url": "https://search.example.com", "num_results": 5},
        )
        return tool

    def _create_webapp_tool(self) -> Tool:
        return Tool.objects.create(
            user=self.user,
            name="WebApp",
            description="WebApp",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="webapp",
            python_path="nova.plugins.webapp",
        )

    def test_text_shell_redirections_and_pipelines_work_for_common_cases(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('echo "hello" > /note.txt')
        async_to_sync(executor.execute)('echo "world" >> /note.txt')
        redirected = async_to_sync(executor.execute)("grep hello < /note.txt")
        copied = async_to_sync(executor.execute)("cat /note.txt | tee /copy.txt | grep world")

        self.assertEqual(async_to_sync(executor.execute)("cat /note.txt"), "hello\nworld\n")
        self.assertEqual(async_to_sync(executor.execute)("cat /copy.txt"), "hello\nworld\n")
        self.assertEqual(redirected, "hello")
        self.assertEqual(copied, "world")

    def test_terminal_supports_semicolon_sequences(self):
        executor = self._build_executor()

        output = async_to_sync(executor.execute)(
            'mkdir -p /tmp/flyer-v8; echo "ok" > /tmp/flyer-v8/status.txt; ls -l /tmp/flyer-v8'
        )

        self.assertIn("/tmp/flyer-v8", output)
        self.assertIn("status.txt", output)
        self.assertEqual(async_to_sync(executor.execute)("cat /tmp/flyer-v8/status.txt"), "ok\n")

    def test_terminal_execute_result_exposes_stdout_stderr_and_status(self):
        executor = self._build_executor()

        success = async_to_sync(executor.execute_result)("pwd")
        failure = async_to_sync(executor.execute_result)("unknowncmd")

        self.assertEqual(success.status, 0)
        self.assertEqual(success.stdout, "/")
        self.assertEqual(success.stderr, "")
        self.assertEqual(failure.status, 1)
        self.assertEqual(failure.stdout, "")
        self.assertIn("Unknown command: unknowncmd", failure.stderr)

    def test_terminal_semicolon_sequences_follow_shell_status_semantics(self):
        executor = self._build_executor()

        output = async_to_sync(executor.execute)(
            'mkdir /tmp/semicolon-test; unknowncmd; echo "done" > /tmp/semicolon-test/result.txt'
        )

        self.assertTrue(
            "command not found" in output or "Unknown command: unknowncmd" in output
        )
        self.assertEqual(async_to_sync(executor.execute)("cat /tmp/semicolon-test/result.txt"), "done\n")

        with self.assertRaises(TerminalCommandError) as cm:
            async_to_sync(executor.execute)("pwd; unknowncmd")

        self.assertIn("Unknown command: unknowncmd", str(cm.exception))

    def test_terminal_semicolon_sequences_reject_empty_segments(self):
        executor = self._build_executor()

        for command in ["pwd;;ls", "; pwd", "pwd;"]:
            with self.subTest(command=command):
                with self.assertRaises(TerminalCommandError):
                    async_to_sync(executor.execute)(command)

    def test_webdav_mount_is_visible_only_when_capability_is_enabled(self):
        plain_executor = self._build_executor()
        webdav_tool = self._create_webdav_tool()
        webdav_executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[webdav_tool])
        )

        plain_listing = async_to_sync(plain_executor.execute)("ls /")
        webdav_listing = async_to_sync(webdav_executor.execute)("ls /")

        self.assertNotIn("webdav/", plain_listing)
        self.assertIn("webdav/", webdav_listing)

    def test_webdav_root_lists_mounts_and_suffixes_collisions(self):
        first_tool = self._create_webdav_tool(name="Docs")
        second_tool = self._create_webdav_tool(name="Docs")
        executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[first_tool, second_tool])
        )

        listing = async_to_sync(executor.execute)("ls /webdav")

        self.assertIn("docs/", listing)
        self.assertIn(f"docs-{second_tool.id}/", listing)

    def test_webdav_paths_are_reserved_without_capability(self):
        executor = self._build_executor()

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("ls /webdav")
        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)('tee /webdav/docs/report.txt --text "hello"')

    def test_webdav_listing_and_cat_use_filesystem_commands(self):
        webdav_tool = self._create_webdav_tool()
        executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[webdav_tool])
        )

        with (
            patch(
                "nova.runtime.vfs.list_webdav_directory",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "name": "notes.txt",
                        "path": "/notes.txt",
                        "type": "file",
                        "mime_type": "text/plain",
                        "size": 12,
                    },
                    {
                        "name": "archive",
                        "path": "/archive",
                        "type": "directory",
                        "mime_type": None,
                        "size": None,
                    },
                ],
            ),
            patch(
                "nova.runtime.vfs.read_webdav_text_file",
                new_callable=AsyncMock,
                return_value="hello from remote",
            ),
        ):
            listing = async_to_sync(executor.execute)("ls /webdav/nextcloud-docs")
            content = async_to_sync(executor.execute)("cat /webdav/nextcloud-docs/notes.txt")

        self.assertIn("notes.txt", listing)
        self.assertIn("archive/", listing)
        self.assertEqual(content, "hello from remote")

    def test_webdav_cat_reports_binary_files_cleanly(self):
        webdav_tool = self._create_webdav_tool()
        executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[webdav_tool])
        )

        with patch(
            "nova.runtime.vfs.read_webdav_text_file",
            new_callable=AsyncMock,
            side_effect=ValueError(
                "Binary file cannot be displayed as text: /report.pdf (application/pdf, 8 bytes)"
            ),
        ):
            with self.assertRaises(TerminalCommandError) as cm:
                async_to_sync(executor.execute)("cat /webdav/nextcloud-docs/report.pdf")

        self.assertIn("Binary file cannot be displayed as text", str(cm.exception))

    def test_webdav_tee_mkdir_rm_and_same_mount_copy_honor_permissions(self):
        write_blocked = self._create_webdav_tool(name="Write Blocked", allow_create_files=False)
        dir_blocked = self._create_webdav_tool(name="Dir Blocked", allow_create_directories=False)
        delete_blocked = self._create_webdav_tool(name="Delete Blocked", allow_delete=False)
        copy_blocked = self._create_webdav_tool(name="Copy Blocked", allow_copy=False)

        write_executor = self._build_executor(TerminalCapabilities(webdav_tools=[write_blocked]))
        dir_executor = self._build_executor(TerminalCapabilities(webdav_tools=[dir_blocked]))
        delete_executor = self._build_executor(TerminalCapabilities(webdav_tools=[delete_blocked]))
        copy_executor = self._build_executor(TerminalCapabilities(webdav_tools=[copy_blocked]))

        with patch(
            "nova.runtime.vfs.stat_webdav_path",
            new_callable=AsyncMock,
            return_value={"exists": False, "path": "/report.txt"},
        ):
            with self.assertRaises(TerminalCommandError):
                async_to_sync(write_executor.execute)('tee /webdav/write-blocked/report.txt --text "hello"')
        with self.assertRaises(TerminalCommandError):
            async_to_sync(dir_executor.execute)("mkdir /webdav/dir-blocked/archive")
        with self.assertRaises(TerminalCommandError):
            async_to_sync(delete_executor.execute)("rm /webdav/delete-blocked/report.txt")
        with (
            patch(
                "nova.runtime.vfs.stat_webdav_path",
                new_callable=AsyncMock,
                return_value={"exists": True, "type": "file", "path": "/a.txt", "mime_type": "text/plain"},
            ),
            patch(
                "nova.runtime.vfs.webdav_copy_path",
                new_callable=AsyncMock,
                side_effect=ValueError("This WebDAV tool does not allow copying paths."),
            ),
        ):
            with self.assertRaises(TerminalCommandError):
                async_to_sync(copy_executor.execute)(
                    "cp /webdav/copy-blocked/a.txt /webdav/copy-blocked/b.txt"
                )

    def test_webdav_cross_boundary_copy_and_move_use_local_filesystem_semantics(self):
        webdav_tool = self._create_webdav_tool(name="Docs")
        executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[webdav_tool])
        )
        async_to_sync(executor.execute)('tee /report.txt --text "hello remote"')

        def _fake_stat(_tool, path):
            normalized = str(path)
            if normalized == "/remote.txt":
                return {"exists": True, "type": "file", "path": normalized, "mime_type": "text/plain"}
            return {"exists": False, "path": normalized}

        with (
            patch(
                "nova.runtime.vfs.write_webdav_bytes",
                new_callable=AsyncMock,
                return_value={
                    "status": "ok",
                    "http_status": 201,
                    "path": "/report.txt",
                    "mime_type": "text/plain",
                    "size": 12,
                },
            ) as mocked_remote_write,
            patch(
                "nova.runtime.vfs.stat_webdav_path",
                new_callable=AsyncMock,
                side_effect=_fake_stat,
            ),
            patch(
                "nova.runtime.vfs.read_webdav_binary_file",
                new_callable=AsyncMock,
                return_value={
                    "path": "/remote.txt",
                    "content": b"from remote",
                    "mime_type": "text/plain",
                    "size": 11,
                },
            ),
            patch(
                "nova.runtime.vfs.webdav_delete_path",
                new_callable=AsyncMock,
                return_value={"status": "ok", "http_status": 204, "path": "/remote.txt"},
            ) as mocked_remote_delete,
        ):
            copied_remote = async_to_sync(executor.execute)("cp /report.txt /webdav/docs/report.txt")
            copied_local = async_to_sync(executor.execute)("cp /webdav/docs/remote.txt /local.txt")
            moved_local = async_to_sync(executor.execute)("mv /webdav/docs/remote.txt /moved.txt")

        self.assertIn("/webdav/docs/report.txt", copied_remote)
        self.assertIn("/local.txt", copied_local)
        self.assertIn("/moved.txt", moved_local)
        self.assertEqual(async_to_sync(executor.execute)("cat /local.txt"), "from remote")
        self.assertEqual(async_to_sync(executor.execute)("cat /moved.txt"), "from remote")
        mocked_remote_write.assert_awaited()
        mocked_remote_delete.assert_awaited_once()

    def test_webdav_find_and_grep_surface_recursive_limit_errors(self):
        webdav_tool = self._create_webdav_tool()
        executor = self._build_executor(
            TerminalCapabilities(webdav_tools=[webdav_tool])
        )

        with patch(
            "nova.runtime.vfs.find_webdav_paths",
            new_callable=AsyncMock,
            side_effect=ValueError(
                "WebDAV recursive traversal exceeded 500 paths. Please target a smaller sub-directory."
            ),
        ):
            with self.assertRaises(TerminalCommandError) as cm:
                async_to_sync(executor.execute)('grep -r "note" /webdav/nextcloud-docs')

        self.assertIn("500 paths", str(cm.exception))

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
        combined = async_to_sync(executor.execute)("date +%F %T")
        combined_utc = async_to_sync(executor.execute)("date -u +%F %T")
        weekday = async_to_sync(executor.execute)("date +%A")
        date_and_weekday = async_to_sync(executor.execute)("date +%Y-%m-%d '+%A'")
        redirected = async_to_sync(executor.execute)("date +%F %T > /tmp/date.txt")

        self.assertRegex(default_output, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+$")
        self.assertRegex(utc_output, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$")
        self.assertRegex(date_only, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(time_only, r"^\d{2}:\d{2}:\d{2}$")
        self.assertRegex(combined, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertRegex(combined_utc, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertTrue(weekday)
        self.assertRegex(date_and_weekday, r"^\d{4}-\d{2}-\d{2} .+$")
        self.assertIn("Wrote", redirected)
        self.assertRegex(async_to_sync(executor.execute)("cat /tmp/date.txt"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_date_rejects_first_format_fragment_without_plus(self):
        executor = self._build_executor(
            TerminalCapabilities(date_time_tool=object())
        )

        with self.assertRaises(TerminalCommandError):
            async_to_sync(executor.execute)("date %F")

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_terminal_supports_shell_substitution_patterns(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(stdout="/\n", stderr="", status=0, cwd_after="/"),
            {"synced_paths": [], "removed_paths": []},
        )
        executor = self._build_executor()

        self.assertEqual(async_to_sync(executor.execute)("echo $(pwd)").strip(), "/")
        self.assertEqual(async_to_sync(executor.execute)("echo `pwd`").strip(), "/")

    def test_terminal_supports_logical_and_and_or(self):
        executor = self._build_executor()

        success = async_to_sync(executor.execute)("pwd && ls /")
        skipped = async_to_sync(executor.execute_result)("unknowncmd && pwd")
        fallback = async_to_sync(executor.execute)("unknowncmd || pwd")
        short_circuit = async_to_sync(executor.execute_result)("pwd || ls /")
        chained = async_to_sync(executor.execute)("unknowncmd || pwd && ls /")

        self.assertIn("skills/", success)
        self.assertEqual(skipped.status, 1)
        self.assertEqual(skipped.skipped_segment_indexes, [2])
        self.assertTrue(
            "command not found" in fallback or "Unknown command: unknowncmd" in fallback
        )
        self.assertIn("/", fallback)
        self.assertEqual(short_circuit.status, 0)
        self.assertEqual(short_circuit.skipped_segment_indexes, [2])
        self.assertIn("skills", chained)

    def test_terminal_pipeline_failure_blocks_following_and_segment(self):
        executor = self._build_executor()

        async_to_sync(executor.execute)('echo "hello" > /tmp/note.txt')

        result = async_to_sync(executor.execute_result)("cat /tmp/note.txt | unknowncmd && pwd")

        self.assertEqual(result.status, 1)
        self.assertIn("Unknown command: unknowncmd", result.stderr)
        self.assertEqual(result.failed_segment_indexes, [1])
        self.assertEqual(result.skipped_segment_indexes, [2])

    def test_terminal_failure_metrics_aggregate_and_sanitize_examples(self):
        executor = self._build_executor()

        for command in [
            "unknowncmd --token secret-value",
            "unknowncmd --token secret-value",
            "ls -z",
        ]:
            with self.assertRaises(TerminalCommandError):
                async_to_sync(executor.execute)(command)

        unknown_metric = TerminalCommandFailureMetric.objects.get(
            head_command="unknowncmd",
            failure_kind="unknown_command",
        )
        invalid_metric = TerminalCommandFailureMetric.objects.get(
            head_command="ls",
            failure_kind="invalid_arguments",
        )

        self.assertEqual(unknown_metric.count, 2)
        self.assertEqual(invalid_metric.count, 1)
        self.assertIn("--token <redacted>", " ".join(unknown_metric.recent_examples))
        self.assertNotIn("secret-value", " ".join(unknown_metric.recent_examples))

    def test_terminal_failure_metrics_record_failed_semicolon_segments_individually(self):
        executor = self._build_executor()

        output = async_to_sync(executor.execute)(
            "pwd; unknowncmd --token secret-value; ls -z || pwd"
        )
        self.assertFalse(TerminalCommandFailureMetric.objects.filter(head_command="pwd").exists())
        self.assertTrue(
            "command not found" in output or "Unknown command: unknowncmd" in output
        )

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_terminal_supports_shell_substitution_in_sandbox_fallback(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(stdout="/\n", stderr="", status=0, cwd_after="/"),
            {"synced_paths": [], "removed_paths": []},
        )
        executor = self._build_executor()

        output = async_to_sync(executor.execute)("echo $(pwd)")
        self.assertIn("/", output.strip())

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_sandbox_result_preserves_raw_exit_status_and_stderr(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(stdout="", stderr="grep: no matches", status=7, cwd_after="/"),
            {"synced_paths": [], "removed_paths": []},
        )
        executor = self._build_executor()

        result = async_to_sync(executor.execute_result)("pip list | grep pandas")

        self.assertEqual(result.status, 7)
        self.assertEqual(result.stderr, "grep: no matches")
        self.assertEqual(result.failed_segment_indexes, [0])

    def test_history_commands_are_available_in_continuous_mode(self):
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous thread",
            mode=Thread.Mode.CONTINUOUS,
        )
        executor = self._build_executor_for_thread(continuous_thread)

        with patch(
            "nova.runtime.terminal.conversation_search",
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
            "nova.runtime.terminal.conversation_get",
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
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="hello\nworld",
                    stderr="",
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(executor.execute)(
                "python --output /results /script.py"
            )

        output_file = async_to_sync(executor.execute)("cat /results/script.stdout.txt")
        self.assertIn("Status: Accepted", result)
        self.assertEqual(output_file, "hello\nworld")
        request = mocked_execute.await_args.args[1]
        self.assertEqual(request.mode, "script")
        self.assertEqual(request.entrypoint, "script.py")

    def test_python_script_syncs_workspace_files_back_into_vfs(self):
        code_tool = self._create_code_execution_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool)
        )
        async_to_sync(executor.execute)("mkdir /project")
        async_to_sync(executor.execute)(
            'tee /project/script.py --text "from pathlib import Path\\nPath(\'out.txt\').write_text(\'done\')"'
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="",
                    stderr="",
                    output_files=(
                        python_service.PythonWorkspaceFile(
                            path="out.txt",
                            content=b"done",
                            mime_type="text/plain",
                        ),
                    ),
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(executor.execute)("python /project/script.py")

        request = mocked_execute.await_args.args[1]
        output_file = async_to_sync(executor.execute)("cat /project/out.txt")
        self.assertEqual(request.mode, "script")
        self.assertEqual(request.entrypoint, "script.py")
        self.assertEqual(request.cwd, ".")
        self.assertTrue(any(item.path == "script.py" for item in request.workspace_files))
        self.assertEqual(output_file, "done")
        self.assertIn("Workspace changes synced", result)

    def test_python_script_keeps_synced_workspace_note_even_when_execution_fails(self):
        code_tool = self._create_code_execution_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool)
        )
        async_to_sync(executor.execute)("mkdir /project")
        async_to_sync(executor.execute)(
            'tee /project/script.py --text "from pathlib import Path\\nPath(\'out.txt\').write_text(\'done\')"'
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Exited with status 1",
                    stdout="partial",
                    stderr="boom",
                    output_files=(
                        python_service.PythonWorkspaceFile(
                            path="out.txt",
                            content=b"done",
                            mime_type="text/plain",
                        ),
                    ),
                ),
            ),
        ):
            result = async_to_sync(executor.execute_result)("python /project/script.py")

        output_file = async_to_sync(executor.execute)("cat /project/out.txt")
        rendered = result.render_text()
        self.assertEqual(result.status, 1)
        self.assertEqual(output_file, "done")
        self.assertIn("Status: Exited with status 1", rendered)
        self.assertIn("Workspace changes synced: /project/out.txt", rendered)
        self.assertNotIn("were not synced", rendered)

    def test_python_workspace_flow_can_publish_webapp_from_same_thread_directory(self):
        code_tool = self._create_code_execution_tool()
        webapp_tool = self._create_webapp_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool, webapp_tool=webapp_tool)
        )
        async_to_sync(executor.execute)("mkdir /webapps")
        async_to_sync(executor.execute)("mkdir /webapps/demo")
        async_to_sync(executor.execute)(
            'tee /webapps/demo/build.py --text "print(\'build site\')"'
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="build site",
                    stderr="",
                    output_files=(
                        python_service.PythonWorkspaceFile(
                            path="index.html",
                            content=b"<!doctype html><html><body>Solar</body></html>",
                            mime_type="text/html",
                        ),
                    ),
                ),
            ) as mocked_execute,
        ):
            python_result = async_to_sync(executor.execute)("python /webapps/demo/build.py")

        request = mocked_execute.await_args.args[1]
        exposed = async_to_sync(executor.execute)('webapp expose /webapps/demo --name "Solar Demo"')
        html_output = async_to_sync(executor.execute)("cat /webapps/demo/index.html")
        webapp = WebApp.objects.get(thread=self.thread)

        self.assertEqual(request.mode, "script")
        self.assertEqual(request.entrypoint, "build.py")
        self.assertEqual(request.cwd, ".")
        self.assertTrue(any(item.path == "build.py" for item in request.workspace_files))
        self.assertIn("Workspace changes synced: /webapps/demo/index.html", python_result)
        self.assertIn("<!doctype html>", html_output)
        self.assertIn("Exposed webapp", exposed)
        self.assertEqual(webapp.source_root, "/webapps/demo")

    def test_python_can_use_attachment_after_staging_it_into_workspace(self):
        code_tool = self._create_code_execution_tool()
        source_message = self.thread.add_message("Use attached data", actor=Actor.USER)
        attachment = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            key=f"fake://{self.user.id}/{self.thread.id}/source/input.txt",
            original_filename=f"/.message_attachments/message_{source_message.id}/input.txt",
            mime_type="text/plain",
            size=12,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents[attachment.key] = b"from attachment"
        vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state=dict(self.base_state),
            skill_registry={},
            source_message_id=source_message.id,
        )
        executor = TerminalExecutor(
            vfs=vfs,
            capabilities=TerminalCapabilities(code_execution_tool=code_tool),
        )
        async_to_sync(executor.execute)("mkdir /project")
        async_to_sync(executor.execute)("cp /inbox/input.txt /project/input.txt")
        async_to_sync(executor.execute)(
            'tee /project/process.py --text "print(\'process attachment\')"'
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="process attachment",
                    stderr="",
                    output_files=(
                        python_service.PythonWorkspaceFile(
                            path="report.txt",
                            content=b"processed",
                            mime_type="text/plain",
                        ),
                    ),
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(executor.execute)("python /project/process.py")

        request = mocked_execute.await_args.args[1]
        report = async_to_sync(executor.execute)("cat /project/report.txt")

        self.assertTrue(any(item.path == "input.txt" for item in request.workspace_files))
        staged_input = next(item for item in request.workspace_files if item.path == "input.txt")
        self.assertEqual(staged_input.content, b"from attachment")
        self.assertEqual(report, "processed")
        self.assertIn("Workspace changes synced: /project/report.txt", result)

    def test_python_workspace_writeback_does_not_delete_existing_thread_files(self):
        code_tool = self._create_code_execution_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool)
        )
        async_to_sync(executor.execute)("mkdir /project")
        async_to_sync(executor.execute)('tee /project/keep.txt --text "keep me"')
        async_to_sync(executor.execute)(
            'tee /project/script.py --text "print(\'attempted cleanup\')"'
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="attempted cleanup",
                    stderr="",
                    output_files=(),
                ),
            ),
        ):
            async_to_sync(executor.execute)("python /project/script.py")

        kept = async_to_sync(executor.execute)("cat /project/keep.txt")
        self.assertEqual(kept, "keep me")

    def test_python_dash_c_with_workdir_syncs_workspace_files_back_into_vfs(self):
        code_tool = self._create_code_execution_tool()
        executor = self._build_executor(
            TerminalCapabilities(code_execution_tool=code_tool)
        )
        async_to_sync(executor.execute)("mkdir /project")

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="ok",
                    stderr="",
                    output_files=(
                        python_service.PythonWorkspaceFile(
                            path="generated.txt",
                            content=b"created from python",
                            mime_type="text/plain",
                        ),
                    ),
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(executor.execute)(
                'python --workdir /project -c "print(\'ok\')"'
            )

        request = mocked_execute.await_args.args[1]
        generated = async_to_sync(executor.execute)("cat /project/generated.txt")
        self.assertEqual(request.mode, "inline")
        self.assertEqual(request.code, "print('ok')")
        self.assertEqual(request.cwd, ".")
        self.assertEqual(generated, "created from python")
        self.assertIn("Workspace changes synced", result)

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
        self.assertIn("mail move <id> --to-special junk", skills["mail.md"])
        self.assertIn("--uid", skills["mail.md"])
        self.assertIn("python --output", skills["python.md"])
        self.assertIn("persistent sandbox terminal", skills["python.md"])
        self.assertIn("current terminal session", skills["python.md"])
        self.assertIn("--workdir /project", skills["python.md"])
        self.assertIn("Copy attachments from `/inbox`", skills["python.md"])
        self.assertIn("date +%F %T", skills["date.md"])
        self.assertIn("server locale", skills["date.md"])
        self.assertIn("pwd", skills["terminal.md"])
        self.assertIn("ls /", skills["terminal.md"])
        self.assertIn("mkdir -p /memory/preferences", skills["terminal.md"])
        self.assertIn('echo "hello" > /note.txt', skills["terminal.md"])
        self.assertIn("Files added from the Files panel live under `/`.", skills["terminal.md"])
        self.assertIn("Use `/inbox` only for files attached to the current user message.", skills["terminal.md"])
        self.assertIn("Use `/history` only for earlier message attachments.", skills["terminal.md"])
        self.assertIn('find / -name "*.pdf"', skills["terminal.md"])
        self.assertIn("sort", skills["terminal.md"])
        self.assertIn("ls -laR /subagents", skills["terminal.md"])
        self.assertIn("printf", skills["terminal.md"])
        self.assertIn("file", skills["terminal.md"])

    def test_skill_registry_subagent_guidance_keeps_thread_scoped_work_in_main_session(self):
        skills = build_skill_registry(
            TerminalCapabilities(subagents=[SimpleNamespace(id=7, name="Image Agent")])
        )

        self.assertIn("subagents.md", skills)
        self.assertIn("Do not delegate thread-scoped cleanup", skills["subagents.md"])
        self.assertIn("webapp publication", skills["subagents.md"])

    def test_skill_registry_adds_calendar_guide_when_calendar_is_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(caldav_tools=[object(), object()])
        )

        self.assertIn("calendar.md", skills)
        self.assertIn("calendar accounts", skills["calendar.md"])
        self.assertIn("--account <selector>", skills["calendar.md"])
        self.assertIn("Recurring events", skills["calendar.md"])

    def test_skill_registry_adds_memory_guide_when_memory_is_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(memory_tool=object())
        )

        self.assertIn("memory.md", skills)
        self.assertIn("memory search", skills["memory.md"])
        self.assertIn("grep", skills["memory.md"])

    def test_skill_registry_adds_webdav_guide_when_webdav_is_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(webdav_tools=[object()])
        )

        self.assertIn("webdav.md", skills)
        self.assertIn("ls /webdav", skills["webdav.md"])
        self.assertIn("cp /report.txt /webdav/<mount>/report.txt", skills["webdav.md"])
        self.assertIn("permissions", skills["webdav.md"])

    def test_skill_registry_adds_search_and_browse_guides_when_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(searxng_tool=object(), browser_tool=object())
        )

        self.assertIn("search.md", skills)
        self.assertIn("browse.md", skills)
        self.assertIn("browse open --result 0", skills["search.md"])
        self.assertIn("browse ls", skills["browse.md"])
        self.assertIn("browse click", skills["browse.md"])
        self.assertIn("browse read", skills["browse.md"])
        self.assertIn("browse text https://example.com", skills["browse.md"])
        self.assertIn('browse elements "img" --output /images.json', skills["browse.md"])
        self.assertIn("browse text > /page.txt", skills["browse.md"])

    def test_skill_registry_adds_mcp_and_api_guides_when_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(mcp_tools=[object()], api_tools=[object()])
        )

        self.assertIn("mcp.md", skills)
        self.assertIn("api.md", skills)
        self.assertIn("mcp schema", skills["mcp.md"])
        self.assertIn("--extract-to", skills["mcp.md"])
        self.assertIn("api schema", skills["api.md"])
        self.assertIn("--output", skills["api.md"])

    def test_skill_registry_adds_webapp_guide_when_enabled(self):
        skills = build_skill_registry(
            TerminalCapabilities(webapp_tool=object())
        )

        self.assertIn("webapp.md", skills)
        self.assertIn("webapp expose", skills["webapp.md"])
        self.assertIn("live", skills["webapp.md"])
        self.assertIn("tee ... --text", skills["webapp.md"])
        self.assertIn("Do not HTML-escape shell operators", skills["webapp.md"])

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
            recursion_limit=4,
        )
        self.thread = Thread.objects.create(user=self.user, subject="Test thread")
        self.thread.add_message("Check the current directory.", Actor.USER)

    def _apply_provider_capabilities(
        self,
        provider,
        *,
        tools="unknown",
        image_input="unknown",
        image_output="unknown",
        image_generation="unknown",
        audio_output="unknown",
    ):
        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "Runtime test metadata",
                "inputs": {
                    "text": "pass",
                    "image": image_input,
                    "pdf": "unknown",
                    "audio": "unknown",
                },
                "outputs": {
                    "text": "pass",
                    "image": image_output,
                    "audio": audio_output,
                },
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": tools,
                    "vision": "pass" if image_input == "pass" else "unknown",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": image_generation,
                    "audio_generation": "unknown",
                },
                "limits": {"context_tokens": 100000},
                "model_state": {},
            }
        )
        provider.refresh_from_db()

    def _create_webdav_tool(
        self,
        *,
        name: str = "Nextcloud Docs",
        root_path: str = "/Documents",
        allow_create_files: bool = True,
        allow_create_directories: bool = True,
        allow_move: bool = True,
        allow_copy: bool = True,
        allow_delete: bool = True,
    ) -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description="WebDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="webdav",
            python_path="nova.plugins.webdav",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "server_url": "https://cloud.example.com/remote.php/dav/files/alice",
                "username": "alice",
                "app_password": "secret",
                "root_path": root_path,
                "allow_create_files": allow_create_files,
                "allow_create_directories": allow_create_directories,
                "allow_move": allow_move,
                "allow_copy": allow_copy,
                "allow_delete": allow_delete,
            },
        )
        return tool

    def _create_caldav_tool(self, *, name: str = "Work Calendar", username: str = "work@example.com") -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name=name,
            description="CalDAV",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def _create_browser_tool(self) -> Tool:
        return Tool.objects.create(
            user=self.user,
            name="Browser",
            description="Browser",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="browser",
            python_path="nova.plugins.browser",
        )

    def _create_searxng_tool(self) -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name="SearXNG",
            description="Search",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="searxng",
            python_path="nova.plugins.search",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={"searxng_url": "https://search.example.com", "num_results": 5},
        )
        return tool

    def test_tool_schemas_include_ask_user_for_main_runtime(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        tool_names = [tool["function"]["name"] for tool in runtime._tool_schemas()]

        self.assertIn("ask_user", tool_names)

    def test_tool_schemas_omit_ask_user_when_disabled(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                allow_ask_user=False,
            ).initialize
        )()

        tool_names = [tool["function"]["name"] for tool in runtime._tool_schemas()]

        self.assertNotIn("ask_user", tool_names)

    def test_runtime_returns_interrupt_result_for_ask_user(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        runtime.provider_client.create_chat_completion = AsyncMock(
            return_value={
                "content": "I need one detail first.",
                "tool_calls": [
                    {
                        "id": "call_ask_1",
                        "name": "ask_user",
                        "arguments": json.dumps(
                            {
                                "question": "Which account should I use?",
                                "schema": {"type": "string", "enum": ["work", "personal"]},
                            }
                        ),
                    }
                ],
            }
        )

        result = async_to_sync(runtime.run)()

        self.assertIsInstance(result, ReactTerminalInterruptResult)
        self.assertEqual(result.question, "Which account should I use?")
        self.assertEqual(result.schema["enum"], ["work", "personal"])
        self.assertEqual(result.resume_context["tool_call_id"], "call_ask_1")
        self.assertEqual(result.resume_context["assistant_message"]["role"], "assistant")
        self.assertEqual(
            result.resume_context["assistant_message"]["tool_calls"][0]["function"]["name"],
            "ask_user",
        )

    def test_runtime_rejects_mixed_ask_user_tool_calls(self):
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
                            "id": "call_ask_1",
                            "name": "ask_user",
                            "arguments": json.dumps({"question": "Which account?"}),
                        },
                        {
                            "id": "call_term_1",
                            "name": "terminal",
                            "arguments": json.dumps({"command": "pwd"}),
                        },
                    ],
                },
                {
                    "content": "Recovered.",
                    "tool_calls": [],
                    "total_tokens": 21,
                },
            ]
        )

        result = async_to_sync(runtime.run)()

        self.assertIsInstance(result, ReactTerminalRunResult)
        self.assertEqual(result.final_answer, "Recovered.")
        self.assertEqual(runtime.vfs.cwd, "/")
        self.assertNotIn("pwd", runtime.vfs.session_state.get("history", []))

    def test_runtime_resume_skips_current_interaction_answer_message(self):
        question_message = self.thread.add_message(
            "**Runtime Agent asks:** Which account?",
            Actor.SYSTEM,
            MessageType.INTERACTION_QUESTION,
        )
        interaction = Interaction.objects.create(
            task=Task.objects.create(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                status=TaskStatus.AWAITING_INPUT,
                progress_logs=[],
            ),
            thread=self.thread,
            agent_config=self.agent,
            origin_name="Runtime Agent",
            question="Which account?",
            status=InteractionStatus.ANSWERED,
            answer="work",
            resume_context={
                "assistant_message": {
                    "role": "assistant",
                    "content": "I need one detail first.",
                    "tool_calls": [
                        {
                            "id": "call_ask_1",
                            "type": "function",
                            "function": {
                                "name": "ask_user",
                                "arguments": json.dumps({"question": "Which account?"}),
                            },
                        }
                    ],
                },
                "tool_call_id": "call_ask_1",
            },
        )
        question_message.interaction = interaction
        question_message.save(update_fields=["interaction"])
        self.thread.add_message(
            "**Answer:** work",
            Actor.USER,
            MessageType.INTERACTION_ANSWER,
            interaction,
        )

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        captured = {}

        async def fake_create_chat_completion(*, messages, tools=None):
            del tools
            captured["messages"] = messages
            return {"content": "Using the work account.", "tool_calls": []}

        runtime.provider_client.create_chat_completion = AsyncMock(side_effect=fake_create_chat_completion)

        result = async_to_sync(runtime.run)(
            resume_context=interaction.resume_context,
            interruption_response={
                "interaction_id": interaction.id,
                "interaction_status": interaction.status,
                "user_response": interaction.answer,
            },
        )

        self.assertEqual(result.final_answer, "Using the work account.")
        joined_contents = "\n".join(str(message.get("content") or "") for message in captured["messages"])
        self.assertNotIn("**Answer:** work", joined_contents)
        self.assertIn('{"status": "answered", "answer": "work"}', joined_contents)

    def test_system_prompt_mentions_ask_user(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()

        self.assertIn("ask_user", prompt)

    def test_system_prompt_mentions_markdown_vfs_file_references(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        automatic_prompt = prompt.split("\n\nAgent instructions:", 1)[0]

        self.assertIn("[label](/path/file.ext)", prompt)
        self.assertIn("![alt](/path/image.png)", prompt)
        self.assertIn("/inbox", prompt)
        self.assertIn("/history", prompt)
        self.assertIn("Files uploaded in the Files panel", prompt)
        self.assertIn("Agent instructions:\nBe concise.", prompt)
        self.assertNotIn("You are", automatic_prompt)
        self.assertNotIn("Nova", automatic_prompt)
        self.assertNotIn("Markdown", automatic_prompt)
        self.assertNotIn("React Terminal", prompt)

    def test_toolless_system_prompt_omits_terminal_instructions(self):
        self._apply_provider_capabilities(self.provider, tools="unsupported")
        tooless_agent = AgentConfig.objects.create(
            user=self.user,
            name="Toolless Agent",
            llm_provider=self.provider,
            system_prompt="",
            recursion_limit=4,
        )

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=tooless_agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("Tool use is unavailable", prompt)
        self.assertIn("Do not call terminal, delegate_to_agent, or ask_user.", prompt)
        self.assertNotIn("Filesystem layout:", prompt)
        self.assertNotIn("Enabled command families", prompt)
        self.assertNotIn("Agent instructions:", prompt)

    def test_delegate_tool_description_is_neutral(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        tool_schema = next(
            item for item in runtime._tool_schemas()
            if item.get("function", {}).get("name") == "delegate_to_agent"
        )

        self.assertNotIn("v2", tool_schema["function"]["description"].lower())

    def test_runtime_mounts_source_message_attachments_under_inbox(self):
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        source_message = self.thread.add_message("Use the attached photo.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=f"/.message_attachments/message_{source_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        stored_contents = {user_file.key: jpeg_bytes}

        async def fake_download_file_content(file_obj):
            return stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            inbox_entries = async_to_sync(runtime.vfs.list_dir)("/inbox")
            content, mime_type = async_to_sync(runtime.vfs.read_bytes)("/inbox/IMG_6433.jpg")

        self.assertTrue(async_to_sync(runtime.vfs.path_exists)("/inbox"))
        self.assertEqual([entry["name"] for entry in inbox_entries], ["IMG_6433.jpg"])
        self.assertEqual(content, jpeg_bytes)
        self.assertEqual(mime_type, "image/jpeg")

    def test_inbox_is_read_only_but_files_can_be_copied_out(self):
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        source_message = self.thread.add_message("Use the attached photo.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=f"/.message_attachments/message_{source_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        stored_contents = {user_file.key: jpeg_bytes}

        async def fake_download_file_content(file_obj):
            return stored_contents[file_obj.key]

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            stored_contents[key] = bytes(content)
            return key

        with (
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            with self.assertRaisesRegex(Exception, "Writing into /inbox"):
                async_to_sync(runtime.vfs.write_file)("/inbox/new.txt", b"nope", mime_type="text/plain")
            with self.assertRaisesRegex(Exception, "Removing files from /inbox"):
                async_to_sync(runtime.vfs.remove)("/inbox/IMG_6433.jpg")

            copied = async_to_sync(runtime.vfs.copy)("/inbox/IMG_6433.jpg", "/tmp/copied.jpg")
            copied_content, copied_mime = async_to_sync(runtime.vfs.read_bytes)("/tmp/copied.jpg")

        self.assertEqual(copied.path, "/tmp/copied.jpg")
        self.assertEqual(copied_content, jpeg_bytes)
        self.assertEqual(copied_mime, "image/jpeg")

    def test_runtime_mounts_previous_attachments_under_history(self):
        older_bytes = b"old"
        current_bytes = b"new"
        older_message = self.thread.add_message("Older attachment.", Actor.USER)
        current_message = self.thread.add_message("Current attachment.", Actor.USER)
        older_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/IMG_1000.jpg",
            mime_type="image/jpeg",
            size=len(older_bytes),
            key="fake://attachment/IMG_1000.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        current_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=current_message,
            original_filename=f"/.message_attachments/message_{current_message.id}/IMG_2000.jpg",
            mime_type="image/jpeg",
            size=len(current_bytes),
            key="fake://attachment/IMG_2000.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        stored_contents = {
            older_file.key: older_bytes,
            current_file.key: current_bytes,
        }

        async def fake_download_file_content(file_obj):
            return stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            root_entries = async_to_sync(runtime.vfs.list_dir)("/")
            history_entries = async_to_sync(runtime.vfs.list_dir)("/history")
            older_entries = async_to_sync(runtime.vfs.list_dir)(f"/history/message-{older_message.id}")
            inbox_entries = async_to_sync(runtime.vfs.list_dir)("/inbox")
            older_content, older_mime = async_to_sync(runtime.vfs.read_bytes)(
                f"/history/message-{older_message.id}/IMG_1000.jpg"
            )

        self.assertTrue(async_to_sync(runtime.vfs.path_exists)("/history"))
        self.assertEqual([entry["name"] for entry in history_entries], [f"message-{older_message.id}"])
        self.assertEqual([entry["name"] for entry in older_entries], ["IMG_1000.jpg"])
        self.assertEqual([entry["name"] for entry in inbox_entries], ["IMG_2000.jpg"])
        self.assertIn("history", [entry["name"] for entry in root_entries])
        self.assertEqual(older_content, older_bytes)
        self.assertEqual(older_mime, "image/jpeg")

    def test_history_is_read_only_but_files_can_be_copied_out(self):
        older_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        older_message = self.thread.add_message("Older attachment.", Actor.USER)
        current_message = self.thread.add_message("Current attachment.", Actor.USER)
        older_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/IMG_1000.jpg",
            mime_type="image/jpeg",
            size=len(older_bytes),
            key="fake://attachment/IMG_1000.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {older_file.key: older_bytes}

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        history_path = f"/history/message-{older_message.id}/IMG_1000.jpg"
        with (
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            with self.assertRaisesRegex(Exception, "Writing into /history"):
                async_to_sync(runtime.vfs.write_file)("/history/nope.txt", b"nope", mime_type="text/plain")
            with self.assertRaisesRegex(Exception, "mkdir is not supported inside /history"):
                async_to_sync(runtime.vfs.mkdir)("/history/newdir")
            with self.assertRaisesRegex(Exception, "Removing files from /history"):
                async_to_sync(runtime.vfs.remove)(history_path)
            with self.assertRaisesRegex(Exception, "Moving files from /history"):
                async_to_sync(runtime.vfs.move)(history_path, "/tmp/moved.jpg")

            copied = async_to_sync(runtime.vfs.copy)(history_path, "/tmp/copied-from-history.jpg")
            copied_content, copied_mime = async_to_sync(runtime.vfs.read_bytes)("/tmp/copied-from-history.jpg")

        self.assertEqual(copied.path, "/tmp/copied-from-history.jpg")
        self.assertEqual(copied_content, older_bytes)
        self.assertEqual(copied_mime, "image/jpeg")

    def test_runtime_history_excludes_compacted_thread_attachments(self):
        older_message = self.thread.add_message("Compacted attachment.", Actor.USER)
        live_message = self.thread.add_message("Live attachment.", Actor.USER)
        current_message = self.thread.add_message("Current message.", Actor.USER)
        older_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/old.jpg",
            mime_type="image/jpeg",
            size=3,
            key="fake://attachment/old.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        live_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=live_message,
            original_filename=f"/.message_attachments/message_{live_message.id}/live.jpg",
            mime_type="image/jpeg",
            size=4,
            key="fake://attachment/live.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {
            older_file.key: b"old",
            live_file.key: b"live",
        }
        AgentThreadSession.objects.create(
            thread=self.thread,
            agent_config=self.agent,
            session_state={
                "cwd": "/",
                "history": [],
                "directories": ["/tmp"],
                SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID: older_message.id,
            },
        )

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            history_paths = async_to_sync(runtime.vfs.find)("/history", "")

        self.assertFalse(any(path.endswith("/old.jpg") for path in history_paths))
        self.assertTrue(any(path.endswith("/live.jpg") for path in history_paths))

    def test_runtime_history_excludes_summarized_continuous_attachments(self):
        continuous_thread = ensure_continuous_thread(self.user)
        older_message = continuous_thread.add_message("Earlier attachment.", Actor.USER)
        summarized_message = continuous_thread.add_message("Summarized attachment.", Actor.USER)
        live_message = continuous_thread.add_message("Still live attachment.", Actor.USER)
        current_message = continuous_thread.add_message("Current attachment.", Actor.USER)
        older_file = UserFile.objects.create(
            user=self.user,
            thread=continuous_thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/older.jpg",
            mime_type="image/jpeg",
            size=5,
            key="fake://attachment/older.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        summarized_file = UserFile.objects.create(
            user=self.user,
            thread=continuous_thread,
            source_message=summarized_message,
            original_filename=f"/.message_attachments/message_{summarized_message.id}/summarized.jpg",
            mime_type="image/jpeg",
            size=5,
            key="fake://attachment/summarized.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        live_file = UserFile.objects.create(
            user=self.user,
            thread=continuous_thread,
            source_message=live_message,
            original_filename=f"/.message_attachments/message_{live_message.id}/live.jpg",
            mime_type="image/jpeg",
            size=4,
            key="fake://attachment/live.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        current_file = UserFile.objects.create(
            user=self.user,
            thread=continuous_thread,
            source_message=current_message,
            original_filename=f"/.message_attachments/message_{current_message.id}/current.jpg",
            mime_type="image/jpeg",
            size=7,
            key="fake://attachment/current.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        day_label = get_day_label_for_user(self.user)
        DaySegment.objects.create(
            user=self.user,
            thread=continuous_thread,
            day_label=day_label,
            starts_at_message=older_message,
            summary_markdown="Summary up to the middle of the day.",
            summary_until_message=summarized_message,
        )
        self._stored_contents = {
            older_file.key: b"older",
            summarized_file.key: b"sum",
            live_file.key: b"live",
            current_file.key: b"current",
        }

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=continuous_thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            history_paths = async_to_sync(runtime.vfs.find)("/history", "")
            inbox_paths = async_to_sync(runtime.vfs.find)("/inbox", "")

        self.assertFalse(any(path.endswith("/older.jpg") for path in history_paths))
        self.assertFalse(any(path.endswith("/summarized.jpg") for path in history_paths))
        self.assertTrue(any(path.endswith("/live.jpg") for path in history_paths))
        self.assertTrue(any(path.endswith("/current.jpg") for path in inbox_paths))

    def test_source_message_prompt_mentions_inbox_paths_for_attachments(self):
        source_message = self.thread.add_message("Please use the attached image.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=build_message_attachment_path(source_message.id, "IMG_6433.jpg"),
            mime_type="image/jpeg",
            size=3,
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename="/runtime/generated-image-1.png",
            mime_type="image/png",
            size=4,
            key="fake://runtime/generated-image-1.png",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )

        async def fake_download_file_content(file_obj):
            self.assertEqual(file_obj.id, user_file.id)
            return b"jpg"

        with patch("nova.tasks.tasks.download_file_content", new=fake_download_file_content):
            prompt = async_to_sync(build_source_message_prompt)(
                source_message,
                provider=self.provider,
            )

        intro_text = prompt[0]["text"] if isinstance(prompt, list) else prompt
        self.assertIn("Attached file:", intro_text)
        self.assertIn("IMG_6433.jpg", intro_text)
        self.assertIn("/inbox/IMG_6433.jpg", intro_text)
        self.assertNotIn("generated-image-1.png", intro_text)

    def _create_webapp_tool(self) -> Tool:
        return Tool.objects.create(
            user=self.user,
            name="WebApp",
            description="WebApp",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="webapp",
            python_path="nova.plugins.webapp",
        )

    def _create_code_execution_tool(self) -> Tool:
        tool = Tool.objects.create(
            user=self.user,
            name="Judge0",
            description="Judge0",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="code_execution",
            python_path="nova.plugins.python",
        )
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            config={"judge0_url": "https://judge0.example.com", "timeout": 5},
        )
        return tool

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
        )
        self.assertEqual(result.final_answer, "The current directory is /.")
        self.assertIn("pwd", session.session_state["history"])

    def test_runtime_executes_python_dash_c_terminal_tool_call(self):
        code_tool = self._create_code_execution_tool()
        self.agent.tools.add(code_tool)
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
                            "id": "call_python_1",
                            "name": "terminal",
                            "arguments": json.dumps({"command": 'python -c "print(45 * 35)"'}),
                        }
                    ],
                },
                {
                    "content": "1575",
                    "tool_calls": [],
                },
            ]
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="1575",
                    stderr="",
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(runtime.run)()

        session = AgentThreadSession.objects.get(
            thread=self.thread,
            agent_config=self.agent,
        )
        self.assertEqual(result.final_answer, "1575")
        self.assertEqual(mocked_execute.await_args.args[1].code, "print(45 * 35)")
        self.assertIn('python -c "print(45 * 35)"', session.session_state["history"])

    def test_runtime_recovers_terminal_tool_call_with_unescaped_inner_quotes(self):
        code_tool = self._create_code_execution_tool()
        self.agent.tools.add(code_tool)
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
                            "id": "call_python_1",
                            "name": "terminal",
                            "arguments": '{"command":"python -c "print(45 * 35)""}',
                        }
                    ],
                },
                {
                    "content": "1575",
                    "tool_calls": [],
                },
            ]
        )

        with (
            patch(
                "nova.plugins.python.service.get_judge0_config",
                new_callable=AsyncMock,
                return_value={"url": "https://judge0.example.com", "timeout": 5},
            ),
            patch(
                "nova.plugins.python.service.execute_python_request",
                new_callable=AsyncMock,
                return_value=python_service.PythonExecutionResult(
                    status_description="Accepted",
                    stdout="1575",
                    stderr="",
                ),
            ) as mocked_execute,
        ):
            result = async_to_sync(runtime.run)()

        self.assertEqual(result.final_answer, "1575")
        self.assertEqual(mocked_execute.await_args.args[1].code, "print(45 * 35)")

    def test_runtime_repairs_html_escaped_terminal_shell_operator_and_records_trace(self):
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
        )
        trace_handler = TaskExecutionTraceHandler(task)
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                task=task,
                trace_handler=trace_handler,
            ).initialize
        )()
        stored_contents: dict[str, bytes] = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return stored_contents[file_obj.key]

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.file_utils.download_file_content", new=fake_download_file_content),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.webapp.service.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            tool_result = async_to_sync(runtime._execute_tool_call)(
                {
                    "id": "call_terminal_1",
                    "name": "terminal",
                    "arguments": json.dumps(
                        {"command": "mkdir -p /x &amp;&amp; touch /x/a.txt"}
                    ),
                }
            )

        session = AgentThreadSession.objects.get(
            thread=self.thread,
            agent_config=self.agent,
        )
        task.refresh_from_db()

        def _find_first_tool_node(node):
            if not isinstance(node, dict):
                return None
            if node.get("type") == "tool":
                return node
            for child in node.get("children", []) or []:
                found = _find_first_tool_node(child)
                if found is not None:
                    return found
            return None

        tool_node = _find_first_tool_node(task.execution_trace.get("root"))

        self.assertNotIn("Tool execution error", tool_result["content"])
        self.assertTrue(async_to_sync(runtime.vfs.path_exists)("/x/a.txt"))
        self.assertIn("mkdir -p /x && touch /x/a.txt", session.session_state["history"])
        self.assertIsNotNone(tool_node)
        self.assertTrue(tool_node["meta"]["input_normalized"])
        self.assertEqual(tool_node["meta"]["normalization_kind"], "html_entity_unescape")
        self.assertIn("&amp;&amp;", tool_node["meta"]["original_command_preview"])
        self.assertIn("&&", tool_node["meta"]["normalized_command_preview"])

    def test_runtime_repairs_html_escaped_terminal_webapp_markup(self):
        webapp_tool = self._create_webapp_tool()
        self.agent.tools.add(webapp_tool)
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        raw_command = (
            'mkdir -p /solar-system && '
            'tee /solar-system/index.html --text "<!DOCTYPE html><html><body>OK</body></html>" && '
            'webapp expose /solar-system --name "Solar System"'
        )
        stored_contents: dict[str, bytes] = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return stored_contents[file_obj.key]

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.file_utils.download_file_content", new=fake_download_file_content),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.webapp.service.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            tool_result = async_to_sync(runtime._execute_tool_call)(
                {
                    "id": "call_webapp_1",
                    "name": "terminal",
                    "arguments": json.dumps({"command": html.escape(raw_command, quote=True)}),
                }
            )

            webapp = WebApp.objects.get(thread=self.thread, user=self.user)
            written_html = async_to_sync(runtime.vfs.read_text)("/solar-system/index.html")

        self.assertNotIn("Tool execution error", tool_result["content"])
        self.assertEqual(webapp.source_root, "/solar-system")
        self.assertIn("<!DOCTYPE html>", written_html)
        self.assertNotIn("&lt;!DOCTYPE html&gt;", written_html)

    def test_runtime_does_not_repair_benign_html_entities_in_terminal_command(self):
        command = 'echo "AT&amp;T"'

        normalized, meta = ReactTerminalRuntime._normalize_model_terminal_command(command)

        self.assertEqual(normalized, command)
        self.assertIsNone(meta)

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

    def test_runtime_marks_model_trace_when_streaming_falls_back(self):
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
        trace_handler = TaskExecutionTraceHandler(task)
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                task=task,
                trace_handler=trace_handler,
                progress_handler=handler,
            ).initialize
        )()

        runtime.provider_client.stream_chat_completion = AsyncMock(
            side_effect=NotImplementedError("Native streaming is not implemented for this provider.")
        )
        runtime.provider_client.create_chat_completion = AsyncMock(
            return_value={
                "content": "Fallback answer.",
                "tool_calls": [],
                "total_tokens": 42,
                "streamed": False,
                "streaming_mode": "none",
            }
        )

        result = async_to_sync(runtime.run)()
        task.refresh_from_db()

        def _find_first_model_node(node):
            if not isinstance(node, dict):
                return None
            if node.get("type") == "model_call":
                return node
            for child in node.get("children", []) or []:
                found = _find_first_model_node(child)
                if found is not None:
                    return found
            return None

        model_node = _find_first_model_node(task.execution_trace.get("root"))

        self.assertEqual(result.final_answer, "Fallback answer.")
        self.assertEqual(task.streamed_markdown, "Fallback answer.")
        self.assertIsNotNone(model_node)
        self.assertEqual(model_node["meta"]["streaming_mode"], "fallback")
        self.assertTrue(model_node["meta"]["streaming_fallback"])

    def test_runtime_closes_browser_session_on_success(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        runtime.provider_client.create_chat_completion = AsyncMock(
            return_value={"content": "Done.", "tool_calls": []}
        )
        runtime.terminal.close = AsyncMock()

        result = async_to_sync(runtime.run)()

        self.assertEqual(result.final_answer, "Done.")
        runtime.terminal.close.assert_awaited_once()

    def test_runtime_closes_browser_session_on_error(self):
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()
        runtime.provider_client.create_chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
        runtime.terminal.close = AsyncMock()

        with self.assertRaises(RuntimeError):
            async_to_sync(runtime.run)()

        runtime.terminal.close.assert_awaited_once()

    def test_runtime_loads_compacted_history_summary(self):
        first = self.thread.add_message("Initial requirement", Actor.USER)
        self.thread.add_message("Recent context", Actor.USER)
        session = AgentThreadSession.objects.create(
            thread=self.thread,
            agent_config=self.agent,
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
            "nova.runtime.agent.load_continuous_context",
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
            exclude_interaction_ids=set(),
        )

    def test_system_prompt_mentions_touch_tee_and_conditional_mailbox_and_date_guidance(self):
        date_tool = Tool.objects.create(
            user=self.user,
            name="Date",
            description="Date",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="date",
            python_path="nova.plugins.datetime",
        )
        first_mail = Tool.objects.create(
            user=self.user,
            name="Work Mail",
            description="Work Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.plugins.mail",
        )
        second_mail = Tool.objects.create(
            user=self.user,
            name="Personal Mail",
            description="Personal Mail",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="email",
            python_path="nova.plugins.mail",
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
        self.assertIn("Runtime instructions:", prompt)
        self.assertIn("The main action surface is the `terminal` tool.", prompt)
        self.assertIn("Use shell-like commands for terminal work.", prompt)
        self.assertIn("Inspect `/skills`", prompt)
        self.assertIn("Use `date` for current date/time queries.", prompt)
        self.assertIn("--mailbox <email>", prompt)
        self.assertIn("Files uploaded in the Files panel are persistent thread files under `/`.", prompt)
        self.assertIn("inspect `/` first", prompt)
        self.assertIn("- /: persistent files for this thread, including files added from the Files panel", prompt)
        self.assertNotIn("/thread", prompt)
        self.assertNotIn("/workspace", prompt)

    def test_system_prompt_with_source_message_keeps_root_files_as_the_first_reflex(self):
        source_message = self.thread.add_message("Check the file please.", Actor.USER)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
                source_message_id=source_message.id,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()

        self.assertIn("Current-message attachments are under `/inbox`", prompt)
        self.assertIn("older live-message attachments are under `/history`", prompt)
        self.assertIn("inspect `/` first", prompt)
        self.assertIn("Only fall back to those mounts", prompt)

    def test_system_prompt_mentions_memory_mount_and_search_guidance(self):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.plugins.memory",
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

    def test_system_prompt_mentions_calendar_commands(self):
        first_calendar = self._create_caldav_tool(name="Work Calendar", username="work@example.com")
        second_calendar = self._create_caldav_tool(name="Personal Calendar", username="personal@example.com")
        self.agent.tools.add(first_calendar, second_calendar)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("calendar", prompt)
        self.assertIn("calendar accounts", prompt)
        self.assertIn("--account <selector>", prompt)
        self.assertIn("Recurring events are readable", prompt)

    def test_system_prompt_mentions_webdav_mount(self):
        webdav_tool = self._create_webdav_tool()
        self.agent.tools.add(webdav_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("/webdav", prompt)
        self.assertIn("remote WebDAV mounts", prompt)

    def test_system_prompt_mentions_search_browse_and_non_persistence(self):
        browser_tool = self._create_browser_tool()
        searxng_tool = self._create_searxng_tool()
        self.agent.tools.add(browser_tool, searxng_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("search", prompt)
        self.assertIn("browse", prompt)
        self.assertIn("current run only", prompt)
        self.assertIn("curl", prompt)
        self.assertIn("wget", prompt)

    def test_system_prompt_mentions_live_webapp_workflow(self):
        webapp_tool = self._create_webapp_tool()
        self.agent.tools.add(webapp_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("webapp expose", prompt)
        self.assertIn("live", prompt)
        self.assertIn("source files", prompt)
        self.assertIn("raw characters", prompt)
        self.assertIn("tee ... --text", prompt)

    def test_system_prompt_mentions_python_direct_workflow(self):
        code_tool = self._create_code_execution_tool()
        self.agent.tools.add(code_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("Use `python` inside", prompt)
        self.assertIn("pip install --user <package>", prompt)
        self.assertIn("--workdir", prompt)
        self.assertIn(
            "Keep thread-scoped file organization, cleanup, and webapp lifecycle work",
            prompt,
        )

    def test_system_prompt_lists_subagent_descriptions_and_response_modes(self):
        child = AgentConfig.objects.create(
            user=self.user,
            name="Python Agent",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="sandboxed Python/code tasks only; isolated workspace",
            default_response_mode=AgentConfig.DefaultResponseMode.TEXT,
        )
        self.agent.agent_tools.add(child)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("Python Agent", prompt)
        self.assertIn("isolated workspace", prompt)
        self.assertIn("text output", prompt)

    def test_system_prompt_mentions_mcp_and_api_command_families(self):
        mcp_tool = Tool.objects.create(
            user=self.user,
            name="Notion MCP",
            description="MCP",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            transport_type=Tool.TransportType.STREAMABLE_HTTP,
        )
        api_tool = Tool.objects.create(
            user=self.user,
            name="CRM API",
            description="API",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        self.agent.tools.add(mcp_tool, api_tool)

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        prompt = runtime.build_system_prompt()
        self.assertIn("mcp tools", prompt)
        self.assertIn("mcp schema", prompt)
        self.assertIn("api operations", prompt)
        self.assertIn("api schema", prompt)

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_memory_is_shared_between_threads_for_same_user(self, mocked_provider):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.plugins.memory",
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
            "/memory/editor.md",
            b"# Editor\n\nUses Vim",
            mime_type="text/markdown",
        )
        content = async_to_sync(runtime_b.vfs.read_text)("/memory/editor.md")

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

        self.assertIn("Continuous threads", prompt)
        self.assertIn("history search", prompt)
        self.assertIn("history get", prompt)

    @patch("nova.runtime.provider_client.ProviderClient.invoke_native_completion", new_callable=AsyncMock)
    def test_native_image_response_writes_generated_file_when_requested(self, mocked_native_completion):
        png_data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmXcAAAAASUVORK5CYII="
        )
        native_provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Images",
            provider_type=ProviderType.OPENROUTER,
            model="openai/gpt-image-1",
            api_key="router-key",
        )
        self._apply_provider_capabilities(
            native_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )
        image_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Runtime Agent",
            llm_provider=native_provider,
            system_prompt="Generate images.",
            recursion_limit=4,
            default_response_mode=AgentConfig.DefaultResponseMode.IMAGE,
        )
        source_message = self.thread.add_message("Create a flyer.", Actor.USER)
        source_message.internal_data = {"response_mode": "image"}
        source_message.save(update_fields=["internal_data"])
        mocked_native_completion.return_value = {
            "text": "Poster ready.",
            "images": [
                {
                    "data": png_data_url,
                    "mime_type": "image/png",
                    "filename": "poster.png",
                }
            ],
            "audio": None,
            "raw_response": {"usage": {"total_tokens": 42}},
        }
        self._stored_contents = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=image_agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime.run)(ephemeral_user_prompt="Create a flyer.")
            generated_paths = async_to_sync(runtime.vfs.find)("/generated", "")
            generated_path = next(path for path in generated_paths if path.endswith("poster.png"))
            generated_content, generated_mime = async_to_sync(runtime.vfs.read_bytes)(generated_path)

        self.assertIn("Poster ready.", result.final_answer)
        self.assertIn("`/generated/poster.png`", result.final_answer)
        self.assertTrue(generated_content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(generated_mime, "image/png")

    def test_native_binary_http_output_uses_safe_download_service(self):
        async def fake_download_http_file(url, **kwargs):
            del kwargs
            self.assertEqual(url, "https://example.com/generated.png")
            return {
                "content": b"png-bytes",
                "mime_type": "image/png",
            }

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        with patch("nova.runtime.agent.download_http_file", new=fake_download_http_file):
            content, mime_type = async_to_sync(runtime._resolve_binary_output_payload)(
                "https://example.com/generated.png",
                default_mime_type="application/octet-stream",
            )

        self.assertEqual(content, b"png-bytes")
        self.assertEqual(mime_type, "image/png")

    def test_native_binary_http_output_propagates_network_policy_errors(self):
        async def fake_download_http_file(url, **kwargs):
            del url, kwargs
            raise NetworkPolicyError("blocked private target")

        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        with patch("nova.runtime.agent.download_http_file", new=fake_download_http_file):
            with self.assertRaises(NetworkPolicyError):
                async_to_sync(runtime._resolve_binary_output_payload)(
                    "http://127.0.0.1/private.png",
                    default_mime_type="image/png",
                )

    @patch("nova.runtime.provider_client.ProviderClient.invoke_native_completion", new_callable=AsyncMock)
    def test_native_image_response_uses_explicit_markdown_generated_path(self, mocked_native_completion):
        png_data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmXcAAAAASUVORK5CYII="
        )
        native_provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Images",
            provider_type=ProviderType.OPENROUTER,
            model="openai/gpt-image-1",
            api_key="router-key",
        )
        self._apply_provider_capabilities(
            native_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )
        image_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Runtime Agent",
            llm_provider=native_provider,
            system_prompt="Generate images.",
            recursion_limit=4,
            default_response_mode=AgentConfig.DefaultResponseMode.IMAGE,
        )
        mocked_native_completion.return_value = {
            "text": "Here is the final flyer.\n\n![Flyer](/generated/flyer-trentemoult.png)",
            "images": [
                {
                    "data": png_data_url,
                    "mime_type": "image/png",
                    "filename": "poster.png",
                }
            ],
            "audio": None,
            "raw_response": {"usage": {"total_tokens": 42}},
        }
        self._stored_contents = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=image_agent,
                ).initialize
            )()

            result = async_to_sync(runtime.run)(ephemeral_user_prompt="Create a flyer.")
            generated_content, generated_mime = async_to_sync(runtime.vfs.read_bytes)(
                "/generated/flyer-trentemoult.png"
            )

        self.assertIn("![Flyer](/generated/flyer-trentemoult.png)", result.final_answer)
        self.assertNotIn("Generated file:", result.final_answer)
        self.assertTrue(generated_content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(generated_mime, "image/png")

    @patch("nova.runtime.provider_client.ProviderClient.invoke_native_completion", new_callable=AsyncMock)
    def test_delegate_to_native_image_subagent_copies_generated_file_back(self, mocked_native_completion):
        png_data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmXcAAAAASUVORK5CYII="
        )
        child_provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Image Child",
            provider_type=ProviderType.OPENROUTER,
            model="openai/gpt-image-1",
            api_key="router-key",
        )
        self._apply_provider_capabilities(
            child_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=child_provider,
            system_prompt="Generate images.",
            recursion_limit=2,
            is_tool=True,
            tool_description="Image child",
            default_response_mode=AgentConfig.DefaultResponseMode.IMAGE,
        )
        self.agent.agent_tools.add(child_agent)
        mocked_native_completion.return_value = {
            "text": "Generated.",
            "images": [
                {
                    "data": png_data_url,
                    "mime_type": "image/png",
                    "filename": "flyer.png",
                }
            ],
            "audio": None,
            "raw_response": {},
        }
        self._stored_contents = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Create a flyer image.",
                input_paths=[],
            )
            copied_paths = async_to_sync(runtime.vfs.find)("/subagents", "")
            copied_image_path = next(path for path in copied_paths if path.endswith("/flyer.png"))
            copied_content, copied_mime = async_to_sync(runtime.vfs.read_bytes)(copied_image_path)

        self.assertIn("finished with 1 output file(s)", result)
        self.assertIn("/subagents/", result)
        self.assertNotIn("/generated/flyer.png", copied_image_path)
        self.assertRegex(copied_image_path, r"^/subagents/image-child-[0-9a-f]{8}/flyer\.png$")
        self.assertIn("Reference them in your final reply with Markdown links or images", result)
        self.assertTrue(copied_content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(copied_mime, "image/png")

    def test_delegate_to_native_image_subagent_includes_copied_inbox_image_in_invocation_request(self):
        png_data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmXcAAAAASUVORK5CYII="
        )
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        child_provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter Image Child",
            provider_type=ProviderType.OPENROUTER,
            model="openai/gpt-image-1",
            api_key="router-key",
        )
        self._apply_provider_capabilities(
            child_provider,
            tools="unsupported",
            image_input="pass",
            image_output="pass",
            image_generation="pass",
        )
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=child_provider,
            system_prompt="Generate images.",
            recursion_limit=2,
            is_tool=True,
            tool_description="Image child",
            default_response_mode=AgentConfig.DefaultResponseMode.IMAGE,
        )
        self.agent.agent_tools.add(child_agent)
        source_message = self.thread.add_message("Edit the attached image.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=f"/.message_attachments/message_{source_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {user_file.key: jpeg_bytes}
        captured = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        async def fake_invoke_native_completion(self, *, invocation_request):
            del self
            captured["request"] = invocation_request
            return {
                "text": "Generated.",
                "images": [
                    {
                        "data": png_data_url,
                        "mime_type": "image/png",
                        "filename": "generated-image-1.png",
                    }
                ],
                "audio": None,
                "raw_response": {},
            }

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.runtime.agent.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch(
                "nova.runtime.provider_client.ProviderClient.invoke_native_completion",
                new=fake_invoke_native_completion,
            ),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Add a stylish hat to the man on the left.",
                input_paths=["/inbox/IMG_6433.jpg"],
            )

        content = captured["request"]["content"]
        image_parts = [
            part for part in content
            if isinstance(part, dict) and part.get("type") == "image"
        ]
        self.assertIn("finished with 1 output file(s)", result)
        self.assertIsInstance(content, list)
        self.assertTrue(any(part.get("type") == "text" for part in content if isinstance(part, dict)))
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["filename"], "IMG_6433.jpg")
        self.assertEqual(image_parts[0]["mime_type"], "image/jpeg")
        self.assertEqual(
            image_parts[0]["data"],
            base64.b64encode(jpeg_bytes).decode("utf-8"),
        )

    def test_subagent_outputs_are_copied_back_under_subagents_directory(self):
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Child Agent",
            llm_provider=self.provider,
            system_prompt="Child",
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
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run),
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
        self.assertIn("child `/tmp` files are not returned", result)
        self.assertTrue(any(path.endswith("/answer.txt") for path in copied_paths))
        self.assertFalse(any(path.endswith("/ignored.txt") for path in copied_paths))
        self.assertEqual(copied_answer, "answer")
        self.assertFalse(
            UserFile.objects.filter(
                user=self.user,
                thread=self.thread,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                original_filename__contains="/delegations/",
            ).exists()
        )

    def test_delegate_to_agent_cleans_internal_runtime_files_after_failure(self):
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Child Agent",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        source_message = self.thread.add_message("Use the attached image.", Actor.USER)
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=build_message_attachment_path(source_message.id, "IMG_6433.jpg"),
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {user_file.key: jpeg_bytes}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ephemeral_user_prompt, ensure_root_trace
            raise RuntimeError("boom")

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Use the attached image.",
                input_paths=["/inbox/IMG_6433.jpg"],
            )

        self.assertIn("Sub-agent failed: boom", result)
        self.assertFalse(
            UserFile.objects.filter(
                user=self.user,
                thread=self.thread,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                original_filename__contains="/delegations/",
            ).exists()
        )

    def test_delegate_to_agent_can_copy_source_message_inbox_attachment(self):
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        source_message = self.thread.add_message("Use the attached photo.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=f"/.message_attachments/message_{source_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents: dict[str, bytes] = {user_file.key: jpeg_bytes}
        seen = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ensure_root_trace
            seen["prompt"] = ephemeral_user_prompt
            seen["inbox_exists"] = await self.vfs.path_exists("/inbox/IMG_6433.jpg")
            seen["content"] = await self.vfs.read_bytes("/inbox/IMG_6433.jpg")
            return ReactTerminalRunResult(
                final_answer="Used the provided photo.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Use the attached image.",
                input_paths=["/inbox/IMG_6433.jpg"],
            )

        self.assertIn("Used the provided photo.", result)
        self.assertTrue(seen["inbox_exists"])
        self.assertIn("/inbox/IMG_6433.jpg", seen["prompt"])
        self.assertEqual(seen["content"], (jpeg_bytes, "image/jpeg"))

    def test_delegate_to_agent_can_copy_history_attachment(self):
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        older_message = self.thread.add_message("Older attached photo.", Actor.USER)
        current_message = self.thread.add_message("Current request.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=len(jpeg_bytes),
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {user_file.key: jpeg_bytes}
        seen = {}

        async def fake_upload_file_to_minio(content, path, mime, thread, user):
            key = f"fake://{user.id}/{thread.id}/{uuid.uuid4().hex}/{path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            return key

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ensure_root_trace
            seen["prompt"] = ephemeral_user_prompt
            seen["inbox_exists"] = await self.vfs.path_exists("/inbox/IMG_6433.jpg")
            seen["content"] = await self.vfs.read_bytes("/inbox/IMG_6433.jpg")
            return ReactTerminalRunResult(
                final_answer="Used the historical photo.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        history_path = f"/history/message-{older_message.id}/IMG_6433.jpg"
        with (
            patch("nova.file_utils.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.upload_file_to_minio", new=fake_upload_file_to_minio),
            patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content),
            patch("nova.models.UserFile.UserFile.delete_storage_object", new=Mock()),
            patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run),
        ):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Use the older image.",
                input_paths=[history_path],
            )

        self.assertIn("Used the historical photo.", result)
        self.assertTrue(seen["inbox_exists"])
        self.assertIn("/inbox/IMG_6433.jpg", seen["prompt"])
        self.assertEqual(seen["content"], (jpeg_bytes, "image/jpeg"))

    def test_native_inbox_prompt_inputs_ignore_history_files(self):
        older_message = self.thread.add_message("Older attachment.", Actor.USER)
        current_message = self.thread.add_message("Current attachment.", Actor.USER)
        older_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=older_message,
            original_filename=f"/.message_attachments/message_{older_message.id}/older.jpg",
            mime_type="image/jpeg",
            size=3,
            key="fake://attachment/older.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        current_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=current_message,
            original_filename=f"/.message_attachments/message_{current_message.id}/current.jpg",
            mime_type="image/jpeg",
            size=4,
            key="fake://attachment/current.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {
            older_file.key: b"old",
            current_file.key: b"new",
        }

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=current_message.id,
                ).initialize
            )()

            prompt_inputs = async_to_sync(runtime._load_native_inbox_prompt_inputs)()

        input_paths = runtime._extract_input_paths_from_prompt_inputs(prompt_inputs)
        self.assertEqual(input_paths, ["/inbox/current.jpg"])

    def test_delegate_to_agent_accepts_composite_subagent_selectors(self):
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        seen = {"prompts": []}

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ensure_root_trace
            seen["prompts"].append(ephemeral_user_prompt)
            return ReactTerminalRunResult(
                final_answer="Handled by composite selector.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()

            first = async_to_sync(runtime._delegate_to_agent)(
                agent_id=f"{child_agent.id}:{child_agent.name}",
                question="Use the id:name selector.",
                input_paths=[],
            )
            second = async_to_sync(runtime._delegate_to_agent)(
                agent_id=f"{child_agent.name} ({child_agent.id})",
                question="Use the name (id) selector.",
                input_paths=[],
            )

        self.assertIn("Handled by composite selector.", first)
        self.assertIn("Handled by composite selector.", second)
        self.assertEqual(len(seen["prompts"]), 2)

    def test_delegate_to_agent_suggests_inbox_path_for_missing_attachment_path(self):
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child tool",
        )
        self.agent.agent_tools.add(child_agent)
        source_message = self.thread.add_message("Use the attached photo.", Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            original_filename=f"/.message_attachments/message_{source_message.id}/IMG_6433.jpg",
            mime_type="image/jpeg",
            size=3,
            key="fake://attachment/IMG_6433.jpg",
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self._stored_contents = {user_file.key: b"jpg"}

        async def fake_download_file_content(file_obj):
            return self._stored_contents[file_obj.key]

        with patch("nova.runtime.vfs.download_file_content", new=fake_download_file_content):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                    source_message_id=source_message.id,
                ).initialize
            )()

            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Use the attached image.",
                input_paths=["/IMG_6433.jpg"],
            )

        self.assertIn("Did you mean /inbox/IMG_6433.jpg?", result)

    def test_subagent_with_webdav_capability_sees_webdav_mount(self):
        webdav_tool = self._create_webdav_tool()
        self.agent.tools.add(webdav_tool)
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="WebDAV Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child WebDAV tool",
        )
        child_agent.tools.add(webdav_tool)
        self.agent.agent_tools.add(child_agent)
        seen = {}

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ephemeral_user_prompt, ensure_root_trace
            seen["webdav"] = await self.vfs.path_exists("/webdav/nextcloud-docs")
            return ReactTerminalRunResult(
                final_answer="Saw WebDAV.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()
            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Check the remote mount.",
                input_paths=[],
            )

        self.assertIn("Saw WebDAV.", result)
        self.assertTrue(seen["webdav"])

    def test_subagent_with_browser_capability_sees_browse_commands(self):
        browser_tool = self._create_browser_tool()
        self.agent.tools.add(browser_tool)
        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Browser Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child browser tool",
        )
        child_agent.tools.add(browser_tool)
        self.agent.agent_tools.add(child_agent)
        seen = {}

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ephemeral_user_prompt, ensure_root_trace
            try:
                await self.terminal.execute("browse current")
            except TerminalCommandError as exc:
                seen["browse_error"] = str(exc)
            return ReactTerminalRunResult(
                final_answer="Saw browse.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run):
            runtime = async_to_sync(
                ReactTerminalRuntime(
                    user=self.user,
                    thread=self.thread,
                    agent_config=self.agent,
                ).initialize
            )()
            result = async_to_sync(runtime._delegate_to_agent)(
                agent_id=str(child_agent.id),
                question="Check browser commands.",
                input_paths=[],
            )

        self.assertIn("Saw browse.", result)
        self.assertIn("No active page", seen["browse_error"])

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_runtime_terminal_trace_meta_includes_semicolon_segments(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(
                stdout="/\ncommand not found\nskills/\n",
                stderr="command not found",
                status=0,
                cwd_after="/",
            ),
            {"synced_paths": [], "removed_paths": []},
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        result = async_to_sync(runtime._execute_terminal_command)(
            "pwd; unknowncmd; ls /"
        )

        self.assertFalse(result.failed)
        self.assertEqual(result.trace_meta["segment_count"], 3)
        self.assertEqual(result.trace_meta["segment_head_commands"], ["pwd", "unknowncmd", "ls"])
        self.assertEqual(result.trace_meta["execution_plane"], "sandbox")
        self.assertNotIn("failed_segment_indexes", result.trace_meta)
        self.assertEqual(result.trace_meta["status"], 0)
        self.assertIn("command not found", result.content)
        self.assertIn("skills", result.content)

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_runtime_terminal_command_preserves_sandbox_nonzero_result_text(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(stdout="", stderr="", status=1, cwd_after="/"),
            {"synced_paths": [], "removed_paths": []},
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        result = async_to_sync(runtime._execute_terminal_command)(
            "pip list | grep -E 'pandas|matplotlib'"
        )

        self.assertFalse(result.failed)
        self.assertEqual(result.trace_meta["status"], 1)
        self.assertEqual(result.content, "Exit status: 1")

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    @patch(
        "nova.runtime.terminal.exec_runner_service.execute_sandbox_shell_command",
        new_callable=AsyncMock,
    )
    def test_runtime_terminal_command_keeps_sandbox_stderr_without_command_error_prefix(self, mocked_execute):
        mocked_execute.return_value = (
            SandboxShellResult(stdout="", stderr="grep: no matches", status=1, cwd_after="/"),
            {"synced_paths": [], "removed_paths": []},
        )
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        result = async_to_sync(runtime._execute_terminal_command)(
            "pip list | grep pandas"
        )

        self.assertFalse(result.failed)
        self.assertEqual(result.trace_meta["status"], 1)
        self.assertEqual(result.content, "stderr: grep: no matches")

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    def test_terminal_sandbox_routing_ignores_memory_substrings_inside_unrelated_paths(self):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.plugins.memory",
        )
        webdav_tool = self._create_webdav_tool()
        self.agent.tools.add(memory_tool, webdav_tool)
        runtime = async_to_sync(
            ReactTerminalRuntime(
                user=self.user,
                thread=self.thread,
                agent_config=self.agent,
            ).initialize
        )()

        self.assertTrue(runtime.terminal._should_route_command_to_sandbox("sed -n '1p' /tmp/memory.txt"))
        self.assertFalse(runtime.terminal._should_route_command_to_sandbox("sed -n '1p' /memory/note.txt"))
        self.assertTrue(runtime.terminal._should_route_command_to_sandbox("sed -n '1p' /tmp/webdav.txt"))
        self.assertFalse(runtime.terminal._should_route_command_to_sandbox("sed -n '1p' /webdav/docs/note.txt"))

    @patch("nova.memory.service.aget_embeddings_provider", new_callable=AsyncMock, return_value=None)
    def test_subagent_with_memory_capability_shares_memory_mount(self, mocked_provider):
        memory_tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.plugins.memory",
        )
        self.agent.tools.add(memory_tool)

        child_agent = AgentConfig.objects.create(
            user=self.user,
            name="Memory Child",
            llm_provider=self.provider,
            system_prompt="Child",
            recursion_limit=2,
            is_tool=True,
            tool_description="Child memory tool",
        )
        child_agent.tools.add(memory_tool)
        self.agent.agent_tools.add(child_agent)

        async def fake_child_run(self, *, ephemeral_user_prompt=None, ensure_root_trace=False):
            del ephemeral_user_prompt, ensure_root_trace
            await self.vfs.write_file(
                "/memory/editor.md",
                b"# Editor\n\nUses Vim",
                mime_type="text/markdown",
            )
            return ReactTerminalRunResult(
                final_answer="Stored memory.",
                real_tokens=None,
                approx_tokens=None,
                max_context=None,
            )

        with patch("nova.runtime.agent.ReactTerminalRuntime.run", new=fake_child_run):
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

        content = async_to_sync(runtime.vfs.read_text)("/memory/editor.md")
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
                "nova.runtime.provider_client.ProviderClient.stream_chat_completion",
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
                "nova.runtime.provider_client.ProviderClient.stream_chat_completion",
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
                "nova.runtime.provider_client.ProviderClient.stream_chat_completion",
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
                "nova.runtime.provider_client.ProviderClient.create_chat_completion",
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
        )
        self.thread = Thread.objects.create(user=self.user, subject="Submission thread")

    def test_v2_message_attachments_use_attachment_uploader(self):
        uploaded = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        dispatcher_task = SimpleNamespace(delay=Mock())
        seen_file_data = {}

        async def fake_thread_file_uploader(thread, user, file_data):
            seen_file_data["value"] = list(file_data)
            return [{"id": 123}], []

        fake_attachment_uploader = Mock(return_value=([{"id": 901, "label": "note.txt"}], []))
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

        self.assertEqual(result.uploaded_file_ids, [])
        self.assertNotIn("value", seen_file_data)
        fake_attachment_uploader.assert_called_once()
