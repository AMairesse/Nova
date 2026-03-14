from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_provider
from nova.tools.artifacts import (
    artifact_attach,
    artifact_ls,
    artifact_read_text,
    artifact_publish_to_files,
    get_skill_instructions,
)


class ArtifactToolsTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Artifacts")
        self.message = self.thread.add_message("See attachment", actor=Actor.USER)
        self.agent = SimpleNamespace(thread=self.thread, user=self.user)

    def test_artifact_ls_lists_thread_artifacts(self):
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="report.pdf",
            mime_type="application/pdf",
        )

        output = async_to_sync(artifact_ls)(self.agent)

        self.assertIn(f"ID: {artifact.id}", output)
        self.assertIn("Kind: pdf", output)
        self.assertIn("report.pdf", output)

    def test_get_skill_instructions_warns_against_guessing_ids(self):
        instructions = get_skill_instructions()

        self.assertIn("Always call artifact_ls or artifact_search before using artifact_ids.", instructions)
        self.assertIn("Never guess artifact_ids.", instructions)

    def test_artifact_attach_returns_reference_payload(self):
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.IMAGE,
            label="diagram.png",
            mime_type="image/png",
        )

        message, payload = async_to_sync(artifact_attach)(self.agent, artifact.id)

        self.assertIn("diagram.png", message)
        self.assertEqual(payload["artifact_id"], artifact.id)
        self.assertEqual(payload["kind"], ArtifactKind.IMAGE)

    def test_artifact_attach_warns_when_pdf_will_use_text_fallback(self):
        provider = create_provider(self.user, name="Fallback PDF Provider")
        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "test",
                "inputs": {"text": "pass", "image": "pass", "pdf": "unknown", "audio": "pass"},
                "outputs": {"text": "pass", "image": "unknown", "audio": "unknown"},
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": "pass",
                    "vision": "pass",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": "unknown",
                    "audio_generation": "unknown",
                },
                "limits": {},
                "model_state": {},
            }
        )
        self.agent.llm_provider = provider
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="report.pdf",
            mime_type="application/pdf",
        )

        message, payload = async_to_sync(artifact_attach)(self.agent, artifact.id)

        self.assertIn("extracted PDF text", message)
        self.assertEqual(payload["provider_delivery"], "text_fallback")

    def test_artifact_attach_rejects_pdf_when_provider_marks_it_unsupported(self):
        provider = create_provider(self.user, name="Unsupported PDF Provider")
        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "test",
                "inputs": {"text": "pass", "image": "pass", "pdf": "unsupported", "audio": "pass"},
                "outputs": {"text": "pass", "image": "unknown", "audio": "unknown"},
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": "pass",
                    "vision": "pass",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": "unknown",
                    "audio_generation": "unknown",
                },
                "limits": {},
                "model_state": {},
            }
        )
        self.agent.llm_provider = provider
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="report.pdf",
            mime_type="application/pdf",
        )

        message, payload = async_to_sync(artifact_attach)(self.agent, artifact.id)

        self.assertIn("does not support PDF attachments", message)
        self.assertIsNone(payload)

    @patch("nova.tools.artifacts.download_file_content", new_callable=AsyncMock)
    def test_artifact_read_text_loads_text_from_storage_when_summary_missing(self, mocked_download):
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=self.message,
            key="users/1/threads/1/note.txt",
            original_filename="/note.txt",
            mime_type="text/plain",
            size=11,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            user_file=user_file,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.TEXT,
            label="note.txt",
            mime_type="text/plain",
            summary_text="",
        )
        mocked_download.return_value = b"hello world"

        result = async_to_sync(artifact_read_text)(self.agent, artifact.id)

        self.assertEqual(result, "hello world")
        mocked_download.assert_awaited_once_with(user_file)

    @patch("nova.tools.artifacts.publish_file_update", new_callable=AsyncMock)
    @patch("nova.tools.artifacts.publish_artifact_to_files", new_callable=AsyncMock)
    def test_artifact_publish_to_files_copies_binary_artifact(
        self,
        mocked_publish_artifact,
        mocked_publish_update,
    ):
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=self.message,
            key="users/1/threads/1/.message_attachments/message_1/report.pdf",
            original_filename="/.message_attachments/message_1/report.pdf",
            mime_type="application/pdf",
            size=128,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            user_file=user_file,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="report.pdf",
            mime_type="application/pdf",
        )
        mocked_publish_artifact.return_value = (99, [])

        result = async_to_sync(artifact_publish_to_files)(self.agent, artifact.id)

        self.assertIn("file ID 99", result)
        mocked_publish_artifact.assert_awaited_once()
        mocked_publish_update.assert_awaited_once()
