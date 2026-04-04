from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async

from nova.external_files import (
    get_external_file_block_reason,
    resolve_binary_attachments_for_ids,
    stage_external_files_as_artifacts,
)
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_user


class ExternalFilesTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="External files")
        self.message = self.thread.add_message("seed", actor=Actor.USER)

    def test_get_external_file_block_reason_rejects_unsafe_extensions(self):
        reason = get_external_file_block_reason(
            filename="payload.exe",
            mime_type="application/octet-stream",
        )

        self.assertIn("unsafe external file extension", reason.lower())

    def test_resolve_binary_attachments_for_ids_uses_text_fallback_for_summary_only_artifacts(self):
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.ANNOTATION,
            label="notes",
            summary_text="hello world",
            search_text="hello world",
        )

        resolved = async_to_sync(resolve_binary_attachments_for_ids)(
            user=self.user,
            thread=self.thread,
            artifact_ids=[artifact.id],
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].content, b"hello world")
        self.assertEqual(resolved[0].mime_type, "text/plain")
        self.assertEqual(resolved[0].filename, "notes.txt")

    def test_resolve_binary_attachments_for_ids_rejects_foreign_thread_files(self):
        other_thread = Thread.objects.create(user=self.user, subject="Other")
        foreign_file = UserFile.objects.create(
            user=self.user,
            thread=other_thread,
            source_message=other_thread.add_message("foreign", actor=Actor.USER),
            key=f"users/{self.user.id}/threads/{other_thread.id}/report.pdf",
            original_filename="/report.pdf",
            mime_type="application/pdf",
            size=12,
            scope=UserFile.Scope.THREAD_SHARED,
        )

        with self.assertRaisesMessage(ValueError, "not found or not accessible"):
            async_to_sync(resolve_binary_attachments_for_ids)(
                user=self.user,
                thread=self.thread,
                file_ids=[foreign_file.id],
            )

    def test_resolve_binary_attachments_for_ids_ignores_cross_tenant_source_artifact_chain(self):
        other_user = create_user(username="external-files-other", email="external-files-other@example.com")
        other_thread = Thread.objects.create(user=other_user, subject="Other tenant")
        other_message = other_thread.add_message("foreign", actor=Actor.USER)
        foreign_file = UserFile.objects.create(
            user=other_user,
            thread=other_thread,
            source_message=other_message,
            key=f"users/{other_user.id}/threads/{other_thread.id}/foreign.pdf",
            original_filename="/foreign.pdf",
            mime_type="application/pdf",
            size=12,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        foreign_artifact = MessageArtifact.objects.create(
            user=other_user,
            thread=other_thread,
            message=other_message,
            user_file=foreign_file,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="foreign.pdf",
            mime_type="application/pdf",
        )
        local_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            source_artifact=foreign_artifact,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.ANNOTATION,
            label="tenant-safe-notes",
            summary_text="tenant-safe fallback",
            search_text="tenant-safe fallback",
        )

        resolved = async_to_sync(resolve_binary_attachments_for_ids)(
            user=self.user,
            thread=self.thread,
            artifact_ids=[local_artifact.id],
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].artifact_id, local_artifact.id)
        self.assertIsNone(resolved[0].user_file_id)
        self.assertEqual(resolved[0].content, b"tenant-safe fallback")
        self.assertEqual(resolved[0].mime_type, "text/plain")
        self.assertEqual(resolved[0].filename, "tenant-safe-notes.txt")

    @patch("nova.external_files.batch_upload_files", new_callable=AsyncMock)
    def test_stage_external_files_preserves_spec_mapping_after_partial_upload_failures(
        self,
        mocked_batch_upload,
    ):
        async def _fake_batch_upload_files(thread, user, upload_specs, **kwargs):
            created = []
            source_message = kwargs["source_message"]
            for spec in (upload_specs[0], upload_specs[2]):
                user_file = await sync_to_async(UserFile.objects.create, thread_sensitive=True)(
                    user=user,
                    thread=thread,
                    source_message=source_message,
                    key=f"users/{user.id}/threads/{thread.id}{spec['path']}",
                    original_filename=spec["path"],
                    mime_type=str(spec.get("mime_type") or "text/plain"),
                    size=len(spec["content"]),
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                )
                created.append(
                    {
                        "id": user_file.id,
                        "path": spec["path"],
                        "filename": spec["path"].rsplit("/", 1)[-1],
                        "mime_type": user_file.mime_type,
                        "size": user_file.size,
                        "scope": UserFile.Scope.MESSAGE_ATTACHMENT,
                        "request_id": spec.get("request_id"),
                    }
                )
            return created, ["Error uploading second.txt"]

        mocked_batch_upload.side_effect = _fake_batch_upload_files

        artifacts, errors = async_to_sync(stage_external_files_as_artifacts)(
            SimpleNamespace(user=self.user, thread=self.thread),
            [
                {
                    "filename": "first.txt",
                    "content": b"first",
                    "label": "First label",
                    "summary_text": "First summary",
                    "search_text": "First search",
                    "metadata": {"external_id": "first"},
                    "origin_locator": {"remote_id": "a1"},
                },
                {
                    "filename": "second.txt",
                    "content": b"second",
                    "label": "Second label",
                    "summary_text": "Second summary",
                    "search_text": "Second search",
                    "metadata": {"external_id": "second"},
                    "origin_locator": {"remote_id": "b2"},
                },
                {
                    "filename": "third.txt",
                    "content": b"third",
                    "label": "Third label",
                    "summary_text": "Third summary",
                    "search_text": "Third search",
                    "metadata": {"external_id": "third"},
                    "origin_locator": {"remote_id": "c3"},
                },
            ],
            origin_type="webdav",
            imported_by_tool="webdav_import_file",
        )

        self.assertEqual(errors, ["Error uploading second.txt"])
        self.assertEqual([artifact.label for artifact in artifacts], ["First label", "Third label"])
        self.assertEqual(
            [artifact.summary_text for artifact in artifacts],
            ["First summary", "Third summary"],
        )
        self.assertEqual(
            [artifact.metadata["external_id"] for artifact in artifacts],
            ["first", "third"],
        )
        self.assertEqual(
            [artifact.metadata["origin_locator"]["remote_id"] for artifact in artifacts],
            ["a1", "c3"],
        )
