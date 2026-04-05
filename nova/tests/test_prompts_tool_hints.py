import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase
from langchain_core.messages import HumanMessage, ToolMessage

from nova.llm.prompts import (
    _get_artifact_context,
    _get_external_file_workflow_guidance,
    _get_file_context,
    _get_skill_catalog,
    _get_tool_prompt_hints,
    _get_user_memory,
    _is_memory_tool_enabled,
    build_nova_system_prompt,
)
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryRecordStatus
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile


User = get_user_model()


class PromptToolHintsTests(TestCase):
    def test_get_tool_prompt_hints_deduplicates_and_strips(self):
        ctx = SimpleNamespace(tool_prompt_hints=["  hint-a  ", "hint-a", "", "hint-b"])

        out = _get_tool_prompt_hints(ctx)

        self.assertEqual(out, ["hint-a", "hint-b"])

    def test_get_tool_prompt_hints_keeps_mailbox_mapping_hint(self):
        mailbox_hint = "Email mailbox map: Work (sending: enabled); Support (sending: disabled)."
        ctx = SimpleNamespace(tool_prompt_hints=[mailbox_hint, ""])

        out = _get_tool_prompt_hints(ctx)

        self.assertEqual(out, [mailbox_hint])

    def test_nova_system_prompt_hides_skill_details_before_activation(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="You are Nova."),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=7),
            tool_prompt_hints=["Use memory_search when needed."],
            skill_catalog={
                "mail": {
                    "label": "Mail",
                    "instructions": [
                        "Email mailbox map: Work (sending: enabled); Support (sending: disabled).",
                    ],
                },
                "files": {
                    "label": "Files",
                    "instructions": ["Use file_ls first."],
                },
            },
            skill_control_tool_names=["load_skill"],
            active_skill_ids=[],
        )
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=ctx),
            state={"messages": [HumanMessage(content="Need help")]},
        )

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            with patch("nova.llm.prompts._get_file_context", new=AsyncMock(return_value="No attached files available.")):
                rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("Tool usage policy:", rendered)
        self.assertIn("Use memory_search when needed.", rendered)
        self.assertIn("On-demand skills available: Files (files), Mail (mail).", rendered)
        self.assertNotIn("Email mailbox map:", rendered)

    def test_nova_system_prompt_shows_active_skill_details_after_load(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="You are Nova."),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=7),
            tool_prompt_hints=[],
            skill_catalog={
                "mail": {
                    "label": "Mail",
                    "instructions": [
                        "Email mailbox map: Work (sending: enabled); Support (sending: disabled).",
                        "Do not send emails from a mailbox where sending is disabled.",
                    ],
                },
            },
            skill_control_tool_names=["load_skill"],
            active_skill_ids=[],
        )
        messages = [
            HumanMessage(content="Organise mes emails"),
            ToolMessage(
                name="load_skill",
                tool_call_id="call-1",
                content=json.dumps({"status": "loaded", "skill": "mail"}),
            ),
        ]
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=ctx),
            state={"messages": messages},
        )

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            with patch("nova.llm.prompts._get_file_context", new=AsyncMock(return_value="No attached files available.")):
                rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("Active skills (current turn):", rendered)
        self.assertIn("- Mail (mail)", rendered)
        self.assertIn("Email mailbox map:", rendered)
        self.assertEqual(ctx.active_skill_ids, ["mail"])

    def test_get_external_file_workflow_guidance_mentions_supported_workflows(self):
        ctx = SimpleNamespace(
            tool_prompt_hints=[
                "Use web_download_file when the user needs the actual file, not just a page summary.",
            ],
            skill_catalog={
                "mail": {"label": "Mail"},
                "webdav": {"label": "WebDAV"},
            },
        )

        rendered = _get_external_file_workflow_guidance(ctx)

        self.assertIn("External file workflow guidance:", rendered)
        self.assertIn("load_skill with mail", rendered)
        self.assertIn("web_download_file", rendered)
        self.assertIn("webdav_import_file", rendered)
        self.assertIn("artifact_ids", rendered)
        self.assertIn("file_ids", rendered)

    def test_nova_system_prompt_includes_external_file_workflow_guidance(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="You are Nova."),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=7),
            tool_prompt_hints=[
                "Use web_download_file when the user needs the actual file, not just a page summary.",
            ],
            skill_catalog={
                "mail": {
                    "label": "Mail",
                    "instructions": [],
                },
                "webdav": {
                    "label": "WebDAV",
                    "instructions": [],
                },
            },
            skill_control_tool_names=["load_skill"],
            active_skill_ids=[],
        )
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=ctx),
            state={"messages": [HumanMessage(content="Download and email a PDF")]},
        )

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            with patch(
                "nova.llm.prompts._get_file_context",
                new=AsyncMock(return_value="No attached files available."),
            ):
                with patch(
                    "nova.llm.prompts._get_artifact_context",
                    new=AsyncMock(return_value="No reusable conversation artifacts available."),
                ):
                    rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("External file workflow guidance:", rendered)
        self.assertIn("list_email_attachments before import_email_attachments", rendered)
        self.assertIn("prefer web_download_file over page-only browsing", rendered)
        self.assertIn("webdav_import_file", rendered)

    def test_build_nova_system_prompt_without_agent_config_uses_fallback(self):
        request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace()), state={})

        rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertEqual(rendered, "You are a helpful assistant.")

    def test_build_nova_system_prompt_renders_today_placeholder_and_no_thread_notice(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="Today is {today}. Keep {missing}."),
            user=SimpleNamespace(id=1),
            thread=None,
            tool_prompt_hints=[],
            skill_catalog={},
            skill_control_tool_names=[],
        )
        request = SimpleNamespace(runtime=SimpleNamespace(context=ctx), state={})

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("Today is ", rendered)
        self.assertIn("{missing}", rendered)
        self.assertIn("No attached files available.", rendered)

    def test_get_skill_catalog_filters_invalid_entries(self):
        catalog = _get_skill_catalog(
            SimpleNamespace(skill_catalog={"mail": {"label": "Mail"}, "": {"label": "Ignored"}, "files": "bad"})
        )

        self.assertEqual(catalog, {"mail": {"label": "Mail"}, "files": {}})

    def test_is_memory_tool_enabled_detects_memory_builtin(self):
        tools_manager = MagicMock()
        tools_manager.filter.return_value = [
            SimpleNamespace(tool_subtype="memory", is_active=True),
            SimpleNamespace(tool_subtype="date", is_active=True),
        ]
        agent_config = SimpleNamespace(tools=tools_manager)

        enabled = async_to_sync(_is_memory_tool_enabled)(agent_config)

        self.assertTrue(enabled)


class PromptHelpersDbTests(DjangoTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt-user",
            email="prompt-user@example.com",
            password="pass123",
        )
        self.thread = Thread.objects.create(user=self.user, subject="Prompt thread")
        self.message = self.thread.add_message("hello", actor=Actor.USER)

    def _create_file(self, filename, mime_type, *, scope=UserFile.Scope.THREAD_SHARED):
        return UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=self.message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/{filename}",
            original_filename=filename,
            mime_type=mime_type,
            size=123,
            scope=scope,
        )

    def test_get_user_memory_lists_paths_and_truncates(self):
        for index in range(12):
            MemoryDocument.objects.create(
                user=self.user,
                virtual_path=f"/memory/theme-{index}.md",
                title=f"Theme {index}",
                content_markdown=f"# Theme {index}\n\nFact {index}",
                status=MemoryRecordStatus.ACTIVE,
            )

        rendered = async_to_sync(_get_user_memory)(self.user)

        self.assertIn("Long-term memory is available through user-scoped documents.", rendered)
        self.assertIn("Known memory paths:", rendered)
        self.assertIn("(+2 more)", rendered)

    def test_get_user_memory_falls_back_when_loading_fails(self):
        with patch("nova.llm.prompts.MemoryDocument.objects.filter", side_effect=RuntimeError("boom")):
            rendered = async_to_sync(_get_user_memory)(self.user)

        self.assertEqual(rendered, "Long-term memory is available.")

    def test_get_file_context_reports_counts_and_fallback(self):
        self._create_file("thread-file.txt", "text/plain")

        rendered = async_to_sync(_get_file_context)(self.thread, self.user)
        self.assertIn("1 file(s) are attached to this thread", rendered)

        with patch("nova.llm.prompts.UserFile.objects.filter", side_effect=RuntimeError("boom")):
            fallback = async_to_sync(_get_file_context)(self.thread, self.user)

        self.assertEqual(fallback, "No attached files available.")

    def test_get_artifact_context_handles_empty_recent_labels_and_errors(self):
        self.assertEqual(
            async_to_sync(_get_artifact_context)(self.thread, self.user),
            "No reusable conversation artifacts available.",
        )

        image_file = self._create_file("photo.png", "image/png", scope=UserFile.Scope.MESSAGE_ATTACHMENT)
        MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            user_file=image_file,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            mime_type="image/png",
            label="",
        )
        fallback_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.TEXT,
            mime_type="text/plain",
            label="",
        )

        rendered = async_to_sync(_get_artifact_context)(self.thread, self.user)

        self.assertIn("Conversation artifacts available:", rendered)
        self.assertIn("photo.png", rendered)
        self.assertIn(f"text-{fallback_artifact.id}", rendered)

        with patch("nova.llm.prompts.MessageArtifact.objects.filter", side_effect=RuntimeError("boom")):
            self.assertIsNone(async_to_sync(_get_artifact_context)(self.thread, self.user))
