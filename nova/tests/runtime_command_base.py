from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from django.contrib.auth.models import User

from nova.models.AgentConfig import AgentConfig
from nova.models.APIToolOperation import APIToolOperation
from nova.models.Message import Actor
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserFile import UserFile
from nova.runtime.capabilities import TerminalCapabilities
from nova.runtime.terminal import TerminalExecutor
from nova.runtime.vfs import VirtualFileSystem


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


class TerminalExecutorCommandTestCase(TransactionTestCase):
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

    def _create_searxng_tool(self) -> Tool:
        tool = self._create_builtin_tool("searxng", name="SearXNG")
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type="none",
            config={"searxng_url": "https://searx.example.com", "max_results": 10},
        )
        return tool

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
